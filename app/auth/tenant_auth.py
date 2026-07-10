from fastapi import Request, HTTPException
from app.core.redis_client import redis_client

async def authenticate_tenant(request: Request) -> dict:
    """
    FastAPI Dependency: Extracts the API key from headers, verifies it against 
    Redis, and attaches the tenant metadata to the request state.
    """
    api_key = request.headers.get("X-API-Key")
    
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    # Fetch the tenant hash from Redis using the provided key
    tenant = await redis_client.hgetall(f"tenant:{api_key}")
    
    if not tenant:
        raise HTTPException(status_code=403, detail="Invalid or revoked API key")

    # Store the tenant dictionary in the request state so later 
    # middlewares (like quotas and rate limiters) can access it
    request.state.tenant = tenant
    
    return tenant