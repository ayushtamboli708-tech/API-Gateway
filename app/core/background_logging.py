from app.core.logger import log_event


def log_usage(tenant_id: str, path: str, status_code: int, latency_ms: float, request_id: str):
    """
    Records API usage metrics for a specific tenant.
    Designed to be run as a FastAPI BackgroundTask to prevent blocking the response.
    """
    log_event(
        event_type="usage",
        tenant_id=tenant_id,
        path=path,
        status=status_code,
        latency_ms=latency_ms,
        request_id=request_id
    )

def log_audit(event: str, request_id: str, **details):
    """
    Records critical security, rate limiting, or configuration events for compliance.
    """
    log_event(
        event_type="audit",
        audit_event=event,
        request_id=request_id,
        **details
    )