import time
from threading import Lock

_STARTED_AT = time.monotonic()
_lock = Lock()
_counters: dict[str, int] = {}


def inc(name: str, amount: int = 1) -> None:
    """Increment an in-process counter. Safe across threadpool tasks."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def render_prometheus() -> str:
    """Render counters and breaker state in Prometheus text exposition format."""
    from app.config import get_settings
    from app.core.circuit_breaker import breaker_snapshot, get_breaker

    # Materialise breakers for every configured service so the gauge is
    # available even before the first request reaches that service.
    for service in get_settings().backends:
        get_breaker(service)

    lines = [
        "# HELP gateway_uptime_seconds Process uptime in seconds.",
        "# TYPE gateway_uptime_seconds gauge",
        f"gateway_uptime_seconds {time.monotonic() - _STARTED_AT:.3f}",
    ]

    for name, value in sorted(snapshot().items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")

    states = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}
    lines.append(
        "# HELP gateway_breaker_state Circuit breaker state "
        "(0=closed, 1=half_open, 2=open)."
    )
    lines.append("# TYPE gateway_breaker_state gauge")
    for service, data in sorted(breaker_snapshot().items()):
        state = states.get(data["state"], 0)
        lines.append(f'gateway_breaker_state{{service="{service}"}} {state}')

    return "\n".join(lines) + "\n"
