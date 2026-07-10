from fastapi import FastAPI, Request, Depends
from fastapi.responses import Response

# Phase 1: Middleware & Proxy
from app.middleware.request_id import RequestIDMiddleware
from app.proxy.forwarder import forward_request

# Phase 2: Auth, Quotas, & Rate Limiting
from app.auth.tenant_auth import authenticate_tenant
from app.core.quota import check_quota
from app.core.rate_limiter import check_rate_limit

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
    and funnels incoming traffic directly to the proxy forwarder.
    """
    # 1. Verify the tenant hasn't exceeded their monthly API allowance
    await check_quota(tenant)
    
    # 2. Verify the tenant isn't spamming the gateway (60s window)
    await check_rate_limit(tenant)
    
    # 3. Forward the request downstream 
    return await forward_request(service, path, request)