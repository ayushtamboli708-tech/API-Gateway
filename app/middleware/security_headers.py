from starlette.middleware.base import BaseHTTPMiddleware

# Conservative defaults for an API edge. Downstream services can override any of
# these headers by setting their own value (we only fill in missing ones).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )

        # Metrics and health output must never be cached by shared proxies.
        if request.url.path in {"/metrics", "/health", "/ready"}:
            response.headers.setdefault("Cache-Control", "no-store")

        return response
