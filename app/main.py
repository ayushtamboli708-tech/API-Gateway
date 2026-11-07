import time
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.tenant_auth import authenticate_tenant
from app.config import get_settings
from app.core import metrics
from app.core import redis_client as redis_store
from app.core.background_logging import log_audit, log_usage
from app.core.logger import log_event, logger
from app.core.quota import QuotaReservation, check_quota, release_quota
from app.core.rate_limiter import RateLimitResult, check_rate_limit
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models import ErrorResponse, HealthResponse, ReadyResponse
from app.proxy.forwarder import close_proxy_client, is_known_service
from app.proxy.retry import forward_with_resilience

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_proxy_client()
    await redis_store.close_redis()


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    description=(
        "Multi-tenant API gateway with API-key auth, Redis-backed rate "
        "limiting and quotas, resilient reverse proxying, and Prometheus "
        "metrics."
    ),
    lifespan=lifespan,
)

if settings.cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(SecurityHeadersMiddleware)

# Reject requests with an unexpected Host header when an allow-list is set.
# This mitigates host-header injection and DNS-rebinding style attacks.
if settings.allowed_hosts and "*" not in settings.allowed_hosts:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )

# Added last so it is the outermost user middleware and still sees CORS
# preflight responses.
app.add_middleware(RequestIDMiddleware)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _tenant_id(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return tenant.get("tenant_id", "unknown")
    return "unknown"


def _audit_tasks(
    request: Request,
    *,
    event: str,
    reason: str,
    status_code: int,
) -> BackgroundTasks:
    tasks = BackgroundTasks()
    tasks.add_task(
        log_audit,
        event=event,
        request_id=_request_id(request),
        reason=reason,
        tenant_id=_tenant_id(request),
        method=request.method,
        path=request.url.path,
        status_code=status_code,
    )
    return tasks


@app.exception_handler(StarletteHTTPException)
async def global_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    """
    Convert every HTTP error into a consistent response and always emit an
    audit log, including errors raised inside auth dependencies.
    """
    tasks = _audit_tasks(
        request,
        event="request_blocked_or_failed",
        reason=str(exc.detail),
        status_code=exc.status_code,
    )
    response = JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": _request_id(request)},
        background=tasks,
    )
    response.headers["X-Request-ID"] = _request_id(request)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Return a generic 500 without leaking internals, but keep a full traceback
    in the server log and an audit record for the tenant.
    """
    logger.exception("Unhandled gateway exception", exc_info=exc)
    metrics.inc("gateway_requests_failed_total")
    tasks = _audit_tasks(
        request,
        event="request_unhandled_error",
        reason=type(exc).__name__,
        status_code=500,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": _request_id(request)},
        headers={"X-Request-ID": _request_id(request)},
        background=tasks,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@app.get("/ready", response_model=ReadyResponse)
async def ready():
    try:
        await redis_store.redis_client.ping()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Redis unavailable",
        ) from exc
    return {"status": "ready"}


@app.get("/metrics")
async def prometheus_metrics():
    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4",
    )


def _apply_metering_headers(
    response,
    rate_limit: RateLimitResult,
    quota: QuotaReservation | None,
) -> None:
    for key, value in rate_limit.headers().items():
        response.headers[key] = value
    if quota is not None:
        for key, value in quota.headers().items():
            response.headers[key] = value


async def _safe_release(
    reservation: QuotaReservation,
    request_id: str,
    tenant_id: str,
) -> None:
    try:
        await release_quota(reservation)
        metrics.inc("gateway_quota_refunds_total")
    except Exception as exc:
        # A failed refund must never mask the original request failure.
        log_event(
            "quota_release_failed",
            request_id=request_id,
            tenant_id=tenant_id,
            reason=type(exc).__name__,
        )


@app.api_route(
    "/api/v1/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        403: {"model": ErrorResponse, "description": "Monthly quota exceeded"},
        404: {"model": ErrorResponse, "description": "Unknown service"},
        413: {"model": ErrorResponse, "description": "Request body too large"},
        429: {"model": ErrorResponse, "description": "Rate limit or auth failure limit"},
        502: {"model": ErrorResponse, "description": "Backend connection failure"},
        503: {"model": ErrorResponse, "description": "Circuit open or Redis unavailable"},
        504: {"model": ErrorResponse, "description": "Backend timeout"},
    },
)
async def gateway(
    service: str,
    path: str,
    request: Request,
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(authenticate_tenant),  # noqa: B008
):
    """
    Catch-all gateway route.

    Order matters:
      1. Reject unknown services before consuming any metered budget.
      2. Rate limit (cheap, per-minute) before quota (monthly billing).
      3. Reserve quota, then refund it if the request is not billable.
    """
    start = time.perf_counter()
    request_id = _request_id(request)

    if not is_known_service(service):
        raise HTTPException(status_code=404, detail="Unknown service")

    rate_limit = await check_rate_limit(tenant)

    try:
        reservation = await check_quota(tenant)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **rate_limit.headers()}
        raise

    try:
        proxied = await forward_with_resilience(service, path, request)
    except HTTPException as exc:
        await _safe_release(reservation, request_id, tenant["tenant_id"])
        exc.headers = {**(exc.headers or {}), **rate_limit.headers()}
        raise
    except Exception:
        await _safe_release(reservation, request_id, tenant["tenant_id"])
        raise

    if proxied.status_code >= 400:
        await _safe_release(reservation, request_id, tenant["tenant_id"])
        _apply_metering_headers(proxied, rate_limit, None)
    else:
        _apply_metering_headers(proxied, rate_limit, reservation)

    latency_ms = (time.perf_counter() - start) * 1000
    background_tasks.add_task(
        log_usage,
        tenant_id=tenant["tenant_id"],
        path=f"/{service}/{path}",
        status_code=proxied.status_code,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    proxied.background = background_tasks

    return proxied
