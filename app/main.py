from fastapi import FastAPI, Request, Depends

# Phase 1: Middleware
from app.middleware.request_id import RequestIDMiddleware

# Phase 2: Auth, Quotas, & Rate Limiting
from app.auth.tenant_auth import authenticate_tenant
from app.core.quota import check_quota
from app.core.rate_limiter import check_rate_limit

# Phase 3: Resilient Proxying
from app.proxy.retry import forward_with_resilience

# Initialize the main application
app = FastAPI(title="Multi-Tenant API Gateway")

# Register the Request ID middleware
app.add_middleware(RequestIDMiddleware)

@app.api_route("/api/v1/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(
    service: str, 
    path: str, 
    request: Request,
    tenant: dict = Depends(authenticate_tenant)
):
    """
    Catch-all route that authenticates the tenant, verifies limits, 
    and safely proxies incoming traffic using circuit breakers and retries.
    """
    # 1. Verify the tenant hasn't exceeded their monthly API allowance
    await check_quota(tenant)
    
    # 2. Verify the tenant isn't spamming the gateway (60s window)
    await check_rate_limit(tenant)
    
    # 3. Forward the request downstream with resilience (retries/circuit breaker)
    return await forward_with_resilience(service, path, request)