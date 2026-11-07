import asyncio
import gzip
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core import metrics
from app.core import quota as quota_module
from app.core import rate_limiter as rate_limit_module
from app.core.circuit_breaker import CircuitBreaker, get_breaker
from app.proxy import forwarder, retry

API_KEY = "key-test"
TENANT = {
    "tenant_id": "tenant_test",
    "plan": "pro",
    "monthly_quota": "10",
    "rate_limit_per_min": "10",
}


def run(coro):
    return asyncio.run(coro)


async def seed_tenant(fake_redis, **overrides):
    data = {**TENANT, **overrides}
    await fake_redis.hset(f"tenant:{API_KEY}", mapping=data)


def make_request(
    *,
    method="GET",
    path="/api/v1/users/test",
    headers=None,
    request_id="trace-123",
    tenant=None,
    body=b"",
):
    raw_headers = [(b"host", b"gateway.local")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("gateway.local", 8000),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, receive=receive)
    request.state.request_id = request_id
    if tenant is not None:
        request.state.tenant = tenant
    return request


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rejected_requests_do_not_join_the_rate_limit_window(fake_redis):
    tenant = {"tenant_id": "tenant_rate", "rate_limit_per_min": "2"}

    async def scenario():
        first = await rate_limit_module.check_rate_limit(tenant)
        second = await rate_limit_module.check_rate_limit(tenant)

        with pytest.raises(HTTPException) as denied:
            await rate_limit_module.check_rate_limit(tenant)

        assert denied.value.status_code == 429
        assert denied.value.headers["Retry-After"]

        # The rejected third request must not have been written to the ZSET.
        assert await fake_redis.zcard("ratelimit:tenant_rate") == 2
        assert first.remaining == 1
        assert second.remaining == 0
        assert first.limit == 2
        assert first.headers()["X-RateLimit-Remaining"] == "1"
        assert metrics.snapshot().get("gateway_rate_limited_total", 0) >= 1

    run(scenario())


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


def test_quota_reserves_caps_and_refunds(fake_redis):
    tenant = {"tenant_id": "tenant_quota", "monthly_quota": "2"}

    async def scenario():
        first = await quota_module.check_quota(tenant)
        second = await quota_module.check_quota(tenant)

        with pytest.raises(HTTPException) as denied:
            await quota_module.check_quota(tenant)

        assert denied.value.status_code == 403
        assert first.used == 1
        assert second.used == 2
        assert denied.value.headers["X-Quota-Remaining"] == "0"
        assert metrics.snapshot().get("gateway_quota_rejected_total", 0) >= 1

        # A failed downstream request should be refunded.
        released = await quota_module.release_quota(second)
        assert released == 1

        # The refunded slot can be reserved again, but the counter stays capped.
        third = await quota_module.check_quota(tenant)
        assert third.used == 2

        with pytest.raises(HTTPException):
            await quota_module.check_quota(tenant)

    run(scenario())


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_half_open_allows_only_one_probe():
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout=10,
        half_open_timeout=5,
    )

    assert breaker.allow_request() is True
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "OPEN"
    assert breaker.allow_request() is False

    breaker.opened_at = time.monotonic() - 11
    assert breaker.allow_request() is True
    assert breaker.state == "HALF_OPEN"

    # A second concurrent request must be rejected while the probe is in flight.
    assert breaker.allow_request() is False
    assert breaker.seconds_until_retry() >= 1

    breaker.record_success()
    assert breaker.state == "CLOSED"
    assert breaker.allow_request() is True


def test_backend_5xx_opens_the_breaker(monkeypatch):
    async def fake_forward(*_args, **_kwargs):
        return Response(status_code=503, content=b"down")

    breaker = get_breaker("users")
    breaker.failure_threshold = 1
    breaker.record_success()

    monkeypatch.setattr(retry, "forward_request", fake_forward)

    response = run(
        retry.forward_with_resilience(
            "users",
            "test",
            SimpleNamespace(method="GET"),
        )
    )

    assert response.status_code == 503
    assert breaker.state == "OPEN"
    assert breaker.allow_request() is False


def test_retries_502_for_idempotent_methods(monkeypatch):
    calls = {"count": 0}

    async def fake_forward(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPException(status_code=502, detail="bad gateway")
        return Response(status_code=200, content=b"ok")

    monkeypatch.setattr(retry, "forward_request", fake_forward)
    monkeypatch.setattr(
        retry,
        "get_settings",
        lambda: SimpleNamespace(
            backends={"users": "http://users"},
            retry_max_attempts=1,
            retry_base_delay=0,
            proxy_total_timeout=5,
        ),
    )

    response = run(
        retry.forward_with_resilience(
            "users",
            "test",
            SimpleNamespace(method="GET"),
        )
    )

    assert response.status_code == 200
    assert calls["count"] == 2


def test_post_is_not_retried(monkeypatch):
    calls = {"count": 0}

    async def fake_forward(*_args, **_kwargs):
        calls["count"] += 1
        raise HTTPException(status_code=502, detail="bad gateway")

    monkeypatch.setattr(retry, "forward_request", fake_forward)

    with pytest.raises(HTTPException) as exc:
        run(
            retry.forward_with_resilience(
                "users",
                "test",
                SimpleNamespace(method="POST"),
            )
        )

    assert exc.value.status_code == 502
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# Proxy header/body handling
# ---------------------------------------------------------------------------


def test_proxy_strips_api_key_and_decodes_compressed_body(monkeypatch):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            status_code=200,
            content=gzip.compress(b"hello"),
            headers=[
                ("content-encoding", "gzip"),
                ("content-type", "text/plain"),
                ("set-cookie", "a=1"),
                ("set-cookie", "b=2"),
            ],
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(forwarder, "proxy_client", mock_client)

    request = make_request(
        headers={"x-api-key": "super-secret", "x-request-id": "trace-123"},
        tenant={"tenant_id": "tenant_test"},
    )

    async def scenario():
        try:
            response = await forwarder.forward_request("users", "test", request)
            body = b"".join(
                [chunk async for chunk in response.body_iterator]
            )
            return response, body
        finally:
            await mock_client.aclose()

    response, body = run(scenario())

    assert "x-api-key" not in captured["headers"]
    assert captured["headers"]["x-request-id"] == "trace-123"
    assert captured["headers"]["x-tenant-id"] == "tenant_test"
    assert captured["headers"]["x-forwarded-for"] == "127.0.0.1"

    assert body == b"hello"
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.headers["content-type"] == "text/plain"
    assert response.headers.getlist("set-cookie") == ["a=1", "b=2"]


def test_request_body_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(forwarder._settings, "max_request_body_bytes", 3)

    request = make_request(body=b"1234")

    async def scenario():
        with pytest.raises(HTTPException) as exc:
            await forwarder.forward_request("users", "test", request)
        return exc.value

    exc = run(scenario())
    assert exc.status_code == 413
    assert exc.detail == "Request body too large"


def test_declared_content_length_is_rejected_early(monkeypatch):
    monkeypatch.setattr(forwarder._settings, "max_request_body_bytes", 3)

    request = make_request(headers={"content-length": "100"})

    async def scenario():
        with pytest.raises(HTTPException) as exc:
            await forwarder.forward_request("users", "test", request)
        return exc.value

    exc = run(scenario())
    assert exc.status_code == 413


# ---------------------------------------------------------------------------
# End-to-end gateway behaviour
# ---------------------------------------------------------------------------


def test_unknown_service_is_rejected_before_metering(fake_redis, monkeypatch):
    from app import main as main_module

    async def fake_forward(*_args, **_kwargs):
        return Response(status_code=200, content=b"ok")

    monkeypatch.setattr(main_module, "forward_with_resilience", fake_forward)

    run(seed_tenant(fake_redis))
    client = TestClient(main_module.app)

    unknown = client.get("/api/v1/nope/test", headers={"X-API-Key": API_KEY})
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Unknown service"
    assert unknown.json()["request_id"]

    valid = client.get("/api/v1/users/test", headers={"X-API-Key": API_KEY})
    assert valid.status_code == 200
    # If the unknown service had been metered, this would be 2.
    assert valid.headers["X-Quota-Used"] == "1"


def test_gateway_proxies_through_real_forwarder(fake_redis, monkeypatch):
    from app import main as main_module

    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            status_code=200,
            content=b"proxied",
            headers={"content-type": "text/plain"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(forwarder, "proxy_client", mock_client)

    run(seed_tenant(fake_redis))
    client = TestClient(main_module.app)

    response = client.get("/api/v1/users/test", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.text == "proxied"
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-Quota-Used"] == "1"
    assert "x-api-key" not in captured["headers"]


def test_5xx_releases_quota_and_returns_metering_headers(fake_redis, monkeypatch):
    from app import main as main_module

    async def failing_forward(*_args, **_kwargs):
        return Response(status_code=500, content=b"boom")

    monkeypatch.setattr(main_module, "forward_with_resilience", failing_forward)

    run(seed_tenant(fake_redis))
    client = TestClient(main_module.app)

    failed = client.get("/api/v1/users/test", headers={"X-API-Key": API_KEY})
    assert failed.status_code == 500
    assert failed.headers["X-RateLimit-Limit"] == "10"
    assert "X-Quota-Used" not in failed.headers

    async def successful_forward(*_args, **_kwargs):
        return Response(status_code=200, content=b"ok")

    monkeypatch.setattr(main_module, "forward_with_resilience", successful_forward)
    success = client.get("/api/v1/users/test", headers={"X-API-Key": API_KEY})

    assert success.status_code == 200
    # The 500 was refunded, so this is usage #1 rather than #2.
    assert success.headers["X-Quota-Used"] == "1"


def test_auth_errors_are_401_and_audited(fake_redis):
    from app import main as main_module

    client = TestClient(main_module.app)

    missing = client.get("/api/v1/users/test")
    assert missing.status_code == 401
    assert missing.json()["request_id"]

    malformed = client.get(
        "/api/v1/users/test",
        headers={"X-API-Key": "not a valid key"},
    )
    assert malformed.status_code == 401

    unknown = client.get(
        "/api/v1/users/test",
        headers={"X-API-Key": "valid-but-unknown"},
    )
    assert unknown.status_code == 401


def test_request_id_is_validated_and_echoed():
    from app import main as main_module

    client = TestClient(main_module.app)

    accepted = client.get("/health", headers={"X-Request-ID": "trace-abc_123"})
    assert accepted.headers["X-Request-ID"] == "trace-abc_123"

    rejected = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert rejected.headers["X-Request-ID"] != "bad id with spaces"
    uuid.UUID(rejected.headers["X-Request-ID"])


def test_health_and_ready(fake_redis):
    from app import main as main_module

    client = TestClient(main_module.app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_metrics_endpoint_exposes_prometheus_text(fake_redis):
    from app import main as main_module

    client = TestClient(main_module.app)
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "gateway_requests_total" in response.text
    assert "gateway_uptime_seconds" in response.text
    assert "gateway_breaker_state" in response.text
