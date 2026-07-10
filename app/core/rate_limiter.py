import time
import uuid
from fastapi import HTTPException
from app.core.redis_client import redis_client

async def check_rate_limit(tenant: dict):
    """
    FastAPI Dependency/Utility: Enforces a 60-second sliding-window rate limit 
    using Redis Sorted Sets (ZSET) in an atomic pipeline.
    """
    tenant_id = tenant["tenant_id"]
    limit = int(tenant["rate_limit_per_min"])
    key = f"ratelimit:{tenant_id}"
    
    now = time.time()
    window = 60  # 60-second sliding window
    
    # Generate a strictly unique member name to prevent microsecond race conditions
    member = f"{now}-{uuid.uuid4()}"
    
    pipe = redis_client.pipeline()
    
    # 1. Add the current request to the sorted set (score = timestamp)
    pipe.zadd(key, {member: now})
    
    # 2. Remove all requests that are older than our 60-second window
    pipe.zremrangebyscore(key, 0, now - window)
    
    # 3. Count how many requests are currently in the valid window
    pipe.zcard(key)
    
    # 4. Set a TTL on the key so it cleans itself up if the tenant goes idle
    pipe.expire(key, window)
    
    # Execute all 4 commands atomically
    _, _, request_count, _ = await pipe.execute()
    
    # Block the request if the count exceeds their allowed limit
    if request_count > limit:
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded"
        )
        
    return request_count