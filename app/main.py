from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.middleware.request_id import RequestIDMiddleware
from app.proxy.forwarder import forward_request

# Initialize the main application
app = FastAPI(title="Multi-Tenant API Gateway")

# Register the Request ID middleware
app.add_middleware(RequestIDMiddleware)

@app.api_route("/api/v1/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(service: str, path: str, request: Request):
    """
    Catch-all route that funnels incoming traffic directly to the proxy forwarder.
    """
    # The forward_request function already returns a properly formatted FastAPI Response,
    # so we can just return it directly!
    return await forward_request(service, path, request)