import asyncio
import time

from fastapi import HTTPException, Request, Response

from app.config import get_settings
from app.core import metrics
from app.core.circuit_breaker import get_breaker
from app.proxy.forwarder import forward_request

# Only safe, non-mutating methods should be retried automatically.
IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}


async def forward_with_resilience(
    service: str,
    path: str,
    request: Request,
) -> Response:
    """
    Wrap the proxy forwarder with circuit breaking, a total request deadline,
    and exponential-backoff retries for idempotent methods.
    """
    settings = get_settings()

    if service not in settings.backends:
        raise HTTPException(status_code=404, detail="Unknown service")

    breaker = get_breaker(service)

    if not breaker.allow_request():
        metrics.inc("gateway_circuit_open_total")
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable (Circuit Open)",
            headers={"Retry-After": str(breaker.seconds_until_retry())},
        )

    max_retries = (
        settings.retry_max_attempts
        if request.method.upper() in IDEMPOTENT_METHODS
        else 0
    )
    delay = settings.retry_base_delay
    deadline = time.monotonic() + settings.proxy_total_timeout

    for attempt in range(max_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            breaker.record_failure()
            raise HTTPException(
                status_code=504,
                detail="Backend service timed out",
            )

        try:
            response = await asyncio.wait_for(
                forward_request(service, path, request),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            metrics.inc("gateway_proxy_errors_total")
            breaker.record_failure()
            if attempt >= max_retries:
                raise HTTPException(
                    status_code=504,
                    detail="Backend service timed out",
                ) from exc
        except HTTPException as exc:
            # 502/504 are infrastructure failures and are retried. A 4xx
            # (for example an unknown service) is returned immediately.
            if exc.status_code not in (502, 504):
                raise

            metrics.inc("gateway_proxy_errors_total")
            breaker.record_failure()
            if attempt >= max_retries:
                raise
        except Exception:
            # Any unexpected failure should count against the breaker, then
            # bubble up to the global exception handler.
            breaker.record_failure()
            raise
        else:
            # A 5xx response means the backend is unhealthy even though the
            # transport succeeded. 4xx responses mean the backend is alive.
            if response.status_code >= 500:
                breaker.record_failure()
            else:
                breaker.record_success()
            return response

        # Exponential backoff, bounded by the remaining total deadline.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            breaker.record_failure()
            raise HTTPException(
                status_code=504,
                detail="Backend service timed out",
            )
        sleep_for = min(delay, remaining)
        metrics.inc("gateway_retries_total")
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        delay *= 2

    # The loop is only exhausted when the deadline is hit.
    breaker.record_failure()
    raise HTTPException(
        status_code=504,
        detail="Backend service timed out",
    )
