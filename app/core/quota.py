from dataclasses import dataclass
from datetime import datetime, timezone

import redis.exceptions
from fastapi import HTTPException

from app.config import get_settings
from app.core import metrics
from app.core import redis_client as redis_store

# Atomically reserve one unit of monthly quota.
#
# The counter is capped at the limit; denied requests do not keep incrementing
# it. A reservation can be released when the downstream request is not
# billable (transport failure, 4xx, 5xx).
#
# KEYS[1] = monthly usage key
# ARGV[1] = quota limit
# ARGV[2] = TTL seconds
# Returns: {allowed(0|1), used, limit}
QUOTA_RESERVE_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

local current = tonumber(redis.call('GET', key) or '0')
if current >= limit then
    return {0, current, limit}
end

local used = redis.call('INCR', key)
if used == 1 then
    redis.call('EXPIRE', key, ttl)
end

return {1, used, limit}
"""

# Release a previously reserved unit without ever going below zero.
QUOTA_RELEASE_SCRIPT = """
local key = KEYS[1]
local current = tonumber(redis.call('GET', key) or '0')
if current <= 0 then
    return 0
end
return redis.call('DECR', key)
"""


def _month_key(tenant_id: str) -> str:
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    return f"usage:{tenant_id}:{current_month}"


def _next_month_reset() -> str:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return reset.isoformat()


@dataclass(frozen=True)
class QuotaReservation:
    key: str
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def headers(self) -> dict[str, str]:
        return {
            "X-Quota-Limit": str(self.limit),
            "X-Quota-Used": str(self.used),
            "X-Quota-Remaining": str(self.remaining),
            "X-Quota-Reset": _next_month_reset(),
        }


async def check_quota(tenant: dict) -> QuotaReservation:
    """Atomically reserve one unit of the tenant's monthly quota."""
    settings = get_settings()
    tenant_id = tenant["tenant_id"]
    limit = int(tenant["monthly_quota"])
    key = _month_key(tenant_id)

    try:
        allowed, used, configured_limit = await redis_store.redis_client.eval(
            QUOTA_RESERVE_SCRIPT,
            1,
            key,
            limit,
            settings.quota_ttl_seconds,
        )
    except redis.exceptions.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Quota backend unavailable",
        ) from exc

    used = int(used)
    configured_limit = int(configured_limit)
    reservation = QuotaReservation(
        key=key,
        used=used,
        limit=configured_limit,
    )

    if not allowed:
        metrics.inc("gateway_quota_rejected_total")
        raise HTTPException(
            status_code=403,
            detail="Monthly API quota exceeded",
            headers=reservation.headers(),
        )

    return reservation


async def release_quota(reservation: QuotaReservation) -> int:
    """Refund a reservation that did not result in a billable response."""
    try:
        return int(
            await redis_store.redis_client.eval(
                QUOTA_RELEASE_SCRIPT,
                1,
                reservation.key,
            )
        )
    except redis.exceptions.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Quota backend unavailable",
        ) from exc
