from datetime import datetime, timezone
from fastapi import HTTPException
from app.core.redis_client import redis_client

async def check_quota(tenant: dict):
    """
    FastAPI Dependency/Utility: Atomically increments the monthly usage 
    counter and verifies it does not exceed the tenant's allowed quota.
    """
    tenant_id = tenant["tenant_id"]
    
    # Generate a key unique to the tenant and the current UTC month
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    month_key = f"usage:{tenant_id}:{current_month}"
    
    # Atomically increment the usage counter in Redis
    current = await redis_client.incr(month_key)
    
    # If this is the first request of the month, set a 31-day expiration
    if current == 1:
        await redis_client.expire(month_key, 60 * 60 * 24 * 31)
        
    # Block the request if the new total exceeds the assigned tier limit
    if current > int(tenant["monthly_quota"]):
        # Distinct 403 status for hard billing/quota limits
        raise HTTPException(
            status_code=403, 
            detail="Monthly API quota exceeded"
        )
        
    return current