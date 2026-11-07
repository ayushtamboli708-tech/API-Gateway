import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.core import metrics

# Accept common tracing formats, but bound the length and character set so a
# client cannot inject arbitrary values into logs or response headers.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        incoming = request.headers.get("X-Request-ID")
        request_id = (
            incoming
            if incoming and REQUEST_ID_PATTERN.fullmatch(incoming)
            else str(uuid.uuid4())
        )

        request.state.request_id = request_id
        metrics.inc("gateway_requests_total")

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        if response.status_code >= 400:
            metrics.inc("gateway_requests_failed_total")

        return response
