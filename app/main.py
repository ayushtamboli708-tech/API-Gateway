import time
from fastapi import FastAPI, Request, Depends, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

# Phase 1: Middleware
from app.middleware.request_id import RequestIDMiddleware

# Phase 2: Auth, Quotas, & Rate Limiting
from app.auth.tenant_auth import authenticate_tenant
from app.core.quota import check_quota
from app.core.rate_limiter import check_rate_limit

# Phase 3: Resilient Proxying
from app.proxy.retry import forward_with_resilience

# Phase 4: Observability
from app.core.background_logging import log_usage, log_audit

app = FastAPI(title="Multi-Tenant API Gateway")

# Register the Request ID middleware
app.add_middleware(RequestIDMiddleware)

# --- GLOBAL EXCEPTION HANDLER ---
@app.exception_handler(HTTPException)
async def global_http_exception_handler(request: Request, exc: HTTPException):
    """
    Intercepts ALL HTTPExceptions (including 401s from dependencies) before 
    they are returned to the client, ensuring audit logs are always written.
    """
    tasks = BackgroundTasks()
    request_id = getattr(request.state, "request_id", "unknown")
    
    # Safely extract the tenant ID if they made it past authentication
    tenant_id = "unknown"
    if hasattr(request.state, "tenant"):
        tenant_id = request.state.tenant.get("tenant_id", "unknown")
        
    # Attach the audit log task to the error response
    tasks.add_task(
        log_audit, 
        event="request_blocked_or_failed", 
        request_id=request_id, 
        reason=exc.detail, 
        tenant_id=tenant_id
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        background=tasks
    )

# --- GATEWAY ROUTE ---
@app.api_route("/api/v1/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(
    service: str, 
    path: str, 
    request: Request,
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(authenticate_tenant)
):
    """
    Catch-all route. Error handling is now delegated to the global exception handler.
    """
    start = time.time()
    request_id = getattr(request.state, "request_id", "unknown")
    
    # 1. Edge Security & Tier Limits 
    await check_quota(tenant)
    await check_rate_limit(tenant)
    
    # 2. Resilient Proxy Handoff
    proxied = await forward_with_resilience(service, path, request)
    
    # 3. Fire-and-forget telemetry for SUCCESSFUL requests
    latency_ms = (time.time() - start) * 1000
    background_tasks.add_task(
        log_usage, 
        tenant_id=tenant["tenant_id"], 
        path=f"/{service}/{path}", 
        status_code=proxied.status_code, 
        latency_ms=latency_ms, 
        request_id=request_id
    )
    
    return proxied