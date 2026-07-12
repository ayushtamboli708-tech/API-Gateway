import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Extract existing ID or generate a new one
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # Attach to request state for internal access
        request.state.request_id = request_id
        
        # Process the request down the stack
        response = await call_next(request)
        
        # Ensure the ID is returned in the final response headers
        response.headers["X-Request-ID"] = request_id
        
        return response