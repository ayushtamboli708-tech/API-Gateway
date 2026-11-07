from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse

from app.config import get_settings

_settings = get_settings()

# Hop-by-hop headers are meaningful for a single transport connection and must
# not be forwarded by a proxy (RFC 7230 section 6.1).
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# The API key belongs to the gateway -> client relationship. It must never be
# forwarded to a downstream service.
REQUEST_HEADERS_EXCLUDED = HOP_BY_HOP_HEADERS | {
    "host",
    "x-api-key",
    "content-length",
}

# We stream decoded bytes (`aiter_bytes`), so `content-encoding` no longer
# describes the bytes we send back. `content-length` is recalculated by the
# server when the response is chunked.
RESPONSE_HEADERS_EXCLUDED = HOP_BY_HOP_HEADERS | {
    "server",
    "date",
    "content-length",
    "content-encoding",
}

TIMEOUT = httpx.Timeout(
    connect=_settings.proxy_connect_timeout,
    read=_settings.proxy_read_timeout,
    write=_settings.proxy_write_timeout,
    pool=_settings.proxy_pool_timeout,
)

proxy_client = httpx.AsyncClient(
    timeout=TIMEOUT,
    limits=httpx.Limits(
        max_connections=_settings.proxy_max_connections,
        max_keepalive_connections=_settings.proxy_max_keepalive_connections,
    ),
    follow_redirects=False,
)


def is_known_service(service: str) -> bool:
    return service in _settings.backends


async def close_proxy_client() -> None:
    """Close the shared HTTPX connection pool. Called from the lifespan."""
    await proxy_client.aclose()


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _target_url(service: str, path: str) -> str:
    """
    Build the upstream URL while preventing SSRF and path-prefix escapes.

    `urljoin` resolves `../` segments and absolute URLs. We then verify that
    the resulting scheme/host/port still match the configured backend, and
    that the resolved path cannot escape a configured base path.
    """
    if "\\" in path or "%2e" in path.lower() or "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid request path")

    base = _settings.backends[service]
    base_parts = urlsplit(base)
    target = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    target_parts = urlsplit(target)

    same_origin = (
        target_parts.scheme == base_parts.scheme
        and target_parts.hostname == base_parts.hostname
        and (target_parts.port or _default_port(target_parts.scheme))
        == (base_parts.port or _default_port(base_parts.scheme))
    )
    if not same_origin:
        raise HTTPException(status_code=400, detail="Invalid request path")

    base_path = base_parts.path.rstrip("/")
    if base_path and not (
        target_parts.path == base_path
        or target_parts.path.startswith(base_path + "/")
    ):
        raise HTTPException(status_code=400, detail="Invalid request path")

    return target


def _request_headers(request: Request) -> dict[str, str]:
    # HTTP header names are case-insensitive. Normalise them so the values we
    # set below replace (rather than duplicate) client-supplied headers such
    # as X-Request-ID.
    headers = {
        key.lower(): value
        for key, value in request.headers.items()
        if key.lower() not in REQUEST_HEADERS_EXCLUDED
    }

    request_id = getattr(request.state, "request_id", None)
    if request_id:
        headers["x-request-id"] = request_id

    tenant = getattr(request.state, "tenant", None)
    if tenant and tenant.get("tenant_id"):
        headers["x-tenant-id"] = tenant["tenant_id"]

    # At the edge we do not trust a client-supplied X-Forwarded-For value;
    # otherwise any client could spoof its source address.
    client_ip = request.client.host if request.client else None
    if client_ip:
        headers["x-forwarded-for"] = client_ip

    headers["x-forwarded-proto"] = request.url.scheme
    original_host = request.headers.get("host")
    if original_host:
        headers["x-forwarded-host"] = original_host

    return headers


async def _read_request_body(request: Request) -> bytes:
    """
    Read and cache the request body, enforcing a hard size limit.

    The cache is required because retries reuse the same Request object and the
    ASGI receive stream can only be consumed once.
    """
    cached = getattr(request.state, "_gateway_body", None)
    if cached is not None:
        return cached

    limit = _settings.max_request_body_bytes
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > limit:
                raise HTTPException(
                    status_code=413,
                    detail="Request body too large",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid Content-Length header",
            ) from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail="Request body too large",
            )
        chunks.append(chunk)

    body = b"".join(chunks)
    request.state._gateway_body = body
    return body


async def forward_request(service: str, path: str, request: Request) -> Response:
    """
    Proxy one request downstream, applying timeouts and sanitising headers.

    The upstream response body is streamed back to the client instead of being
    buffered in gateway memory.
    """
    if service not in _settings.backends:
        raise HTTPException(status_code=404, detail="Unknown service")

    target_url = _target_url(service, path)
    body = await _read_request_body(request)

    upstream_request = proxy_client.build_request(
        method=request.method,
        url=target_url,
        headers=_request_headers(request),
        content=body,
        params=request.query_params,
    )

    try:
        proxied = await proxy_client.send(upstream_request, stream=True)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="Backend service timed out",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Backend communication failed",
        ) from exc

    async def body_iterator():
        try:
            async for chunk in proxied.aiter_bytes():
                yield chunk
        finally:
            await proxied.aclose()

    response = StreamingResponse(
        body_iterator(),
        status_code=proxied.status_code,
    )

    # Preserve duplicate headers such as Set-Cookie by writing raw headers
    # instead of collapsing them into a dict.
    for key, value in proxied.headers.multi_items():
        if key.lower() in RESPONSE_HEADERS_EXCLUDED:
            continue
        response.raw_headers.append(
            (key.encode("latin-1"), value.encode("latin-1"))
        )

    response.headers["Via"] = "1.1 api-gateway"
    return response
