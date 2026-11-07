import re

import redis.exceptions
from fastapi import HTTPException, Request

from app.core import redis_client as redis_store

# API keys are opaque, but bounding their shape prevents both absurdly large
# Redis keys and accidental key injection.
API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

REQUIRED_TENANT_FIELDS = {"tenant_id", "monthly_quota", "rate_limit_per_min"}


async def authenticate_tenant(request: Request) -> dict[str, str]:
    """
    Resolve the X-API-Key header to a tenant profile stored in Redis.
    """
    api_key = request.headers.get("x-api-key")

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if not API_KEY_PATTERN.fullmatch(api_key):
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    try:
        tenant = await redis_store.redis_client.hgetall(f"tenant:{api_key}")
    except redis.exceptions.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication backend unavailable",
        ) from exc

    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

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

    request.state.tenant = tenant
    return tenant
