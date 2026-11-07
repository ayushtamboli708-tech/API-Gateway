import re

import redis.exceptions
from fastapi import HTTPException, Request

from app.config import get_settings
from app.core import metrics
from app.core import redis_client as redis_store

# API keys are opaque, but bounding their shape prevents both absurdly large
# Redis keys and accidental key injection.
API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

REQUIRED_TENANT_FIELDS = {"tenant_id", "monthly_quota", "rate_limit_per_min"}


def _failure_key(request: Request) -> str:
    """
    Rate-limit invalid credentials per client IP.

    We deliberately use the transport peer address rather than
    X-Forwarded-For: at the edge that header is attacker-controlled. If the
    gateway is deployed behind a trusted load balancer, configure the app to
    trust that proxy and adapt this value accordingly.
    """
    client = request.client
    return f"authfail:{client.host if client else 'unknown'}"


async def _check_failure_budget(request: Request) -> None:
    settings = get_settings()
    key = _failure_key(request)

    try:
        current = int(await redis_store.redis_client.get(key) or 0)
        if current < settings.auth_failure_limit:
            return
        ttl = await redis_store.redis_client.ttl(key)
    except redis.exceptions.RedisError:
        # If Redis is unavailable, authentication itself will fail with 503;
        # never fail open with a misleading 429.
        return

    metrics.inc("gateway_auth_throttled_total")
    raise HTTPException(
        status_code=429,
        detail="Too many authentication failures",
        headers={"Retry-After": str(max(1, int(ttl)))},
    )


async def _record_failure(request: Request) -> None:
    settings = get_settings()

    try:
        count = await redis_store.redis_client.incr(_failure_key(request))
        if count == 1:
            await redis_store.redis_client.expire(
                _failure_key(request),
                settings.auth_failure_window_seconds,
            )
    except redis.exceptions.RedisError:
        # Failure accounting is best-effort; it must not mask the 401.
        return

    metrics.inc("gateway_auth_failures_total")


async def _clear_failures(request: Request) -> None:
    try:
        await redis_store.redis_client.delete(_failure_key(request))
    except redis.exceptions.RedisError:
        return


async def _reject_invalid(request: Request, detail: str) -> None:
    """
    Enforce the per-IP failure budget, then count this failure and return 401.

    The budget is checked before recording, so requests above the limit get a
    429 without extending the window (the same non-poisoning principle used by
    the traffic rate limiter).
    """
    await _check_failure_budget(request)
    await _record_failure(request)
    raise HTTPException(status_code=401, detail=detail)


async def authenticate_tenant(request: Request) -> dict[str, str]:
    """
    Resolve the X-API-Key header to a tenant profile stored in Redis.

    Invalid credentials are rate-limited per client IP so the gateway cannot
    be used as an unauthenticated Redis/API-key oracle. Valid keys are never
    throttled: authentication happens first, and only failures are counted.
    """
    api_key = request.headers.get("x-api-key")

    if not api_key:
        await _reject_invalid(request, "Missing X-API-Key header")

    if not API_KEY_PATTERN.fullmatch(api_key):
        await _reject_invalid(request, "Invalid or revoked API key")

    try:
        tenant = await redis_store.redis_client.hgetall(f"tenant:{api_key}")
    except redis.exceptions.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication backend unavailable",
        ) from exc

    if not tenant:
        await _reject_invalid(request, "Invalid or revoked API key")

    if not REQUIRED_TENANT_FIELDS.issubset(tenant):
        raise HTTPException(
            status_code=500,
            detail="Tenant configuration is invalid",
        )

    try:
        int(tenant["monthly_quota"])
        int(tenant["rate_limit_per_min"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Tenant configuration is invalid",
        ) from exc

    await _clear_failures(request)
    request.state.tenant = tenant
    return tenant
