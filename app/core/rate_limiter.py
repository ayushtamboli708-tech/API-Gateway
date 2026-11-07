import math
import time
import uuid
from dataclasses import dataclass

import redis.exceptions
from fastapi import HTTPException

from app.config import get_settings
from app.core import metrics
from app.core import redis_client as redis_store

# Atomic sliding-window check.
#
# The request is added to the sorted set only when it is allowed. This is
# important: if rejected requests were inserted, a client that keeps sending
# traffic would keep the window saturated and delay its own recovery.
#
# KEYS[1] = rate-limit key
# ARGV[1] = limit
# ARGV[2] = window in milliseconds
# ARGV[3] = unique member id
# Returns: {allowed(0|1), count, reset_at_ms}
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local window_start = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_at = now_ms + window_ms
    if oldest[2] then
        reset_at = tonumber(oldest[2]) + window_ms
    end
    return {0, count, reset_at}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset_at = now_ms + window_ms
if oldest[2] then
    reset_at = tonumber(oldest[2]) + window_ms
end

return {1, count + 1, reset_at}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at_ms: int
    request_count: int

    def headers(self) -> dict[str, str]:
        reset_seconds = max(0, math.ceil(self.reset_at_ms / 1000))
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(reset_seconds),
        }
        if not self.allowed:
            retry_after = max(
                1,
                math.ceil((self.reset_at_ms - (time.time() * 1000)) / 1000),
            )
            headers["Retry-After"] = str(retry_after)
        return headers


async def check_rate_limit(tenant: dict) -> RateLimitResult:
    """
    Enforce a per-tenant sliding-window rate limit using Redis sorted sets.

    Raises 429 only after the atomic script has determined that the request
    would exceed the limit. Rejected requests are never written to the window.
    """
    settings = get_settings()
    tenant_id = tenant["tenant_id"]
    limit = int(tenant["rate_limit_per_min"])

    key = f"ratelimit:{tenant_id}"
    window_ms = settings.rate_limit_window_seconds * 1000
    member = f"{time.time_ns()}-{uuid.uuid4()}"

    try:
        allowed, count, reset_at_ms = await redis_store.redis_client.eval(
            RATE_LIMIT_SCRIPT,
            1,
            key,
            limit,
            window_ms,
            member,
        )
    except redis.exceptions.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Rate limiting backend unavailable",
        ) from exc

    count = int(count)
    remaining = max(0, limit - count) if allowed else 0
    result = RateLimitResult(
        allowed=bool(allowed),
        limit=limit,
        remaining=remaining,
        reset_at_ms=int(reset_at_ms),
        request_count=count,
    )

    if not result.allowed:
        metrics.inc("gateway_rate_limited_total")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers=result.headers(),
        )

    return result
