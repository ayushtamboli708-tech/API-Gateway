import time

from app.config import get_settings


class CircuitBreaker:
    """
    A CLOSED -> OPEN -> HALF_OPEN state machine.

    While HALF_OPEN, only one probe request is allowed through. This prevents
    a thundering herd from hitting a service that is just recovering.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_timeout: float = 5.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_timeout = half_open_timeout

        self.failures = 0
        self.state = "CLOSED"
        self.opened_at: float | None = None
        self._half_open_probe_at: float | None = None
        self._half_open_in_flight = False

    def record_success(self) -> None:
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = None
        self._half_open_probe_at = None
        self._half_open_in_flight = False

    def record_failure(self) -> None:
        if self.state == "HALF_OPEN":
            self._open()
            return

        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._open()

    def allow_request(self) -> bool:
        if self.state == "CLOSED":
            return True

        if self.state == "OPEN":
            if self.opened_at is None:
                self._open()
                return False

            if time.monotonic() - self.opened_at >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                self._half_open_in_flight = True
                self._half_open_probe_at = time.monotonic()
                return True

            return False

        # HALF_OPEN: allow exactly one probe at a time. If a probe hangs and
        # never reports back, eventually allow a new probe.
        if self._half_open_in_flight:
            if (
                self._half_open_probe_at is not None
                and time.monotonic() - self._half_open_probe_at
                >= self.half_open_timeout
            ):
                self._half_open_probe_at = time.monotonic()
                return True
            return False

        self._half_open_in_flight = True
        self._half_open_probe_at = time.monotonic()
        return True

    def seconds_until_retry(self) -> int:
        if self.state == "OPEN" and self.opened_at is not None:
            remaining = self.recovery_timeout - (time.monotonic() - self.opened_at)
            return max(0, int(remaining) + (1 if remaining > 0 else 0))

        if self.state == "HALF_OPEN":
            if self._half_open_probe_at is not None:
                remaining = self.half_open_timeout - (
                    time.monotonic() - self._half_open_probe_at
                )
                return max(1, int(remaining) + (1 if remaining > 0 else 0))
            return 1

        return 0

    def _open(self) -> None:
        self.state = "OPEN"
        self.opened_at = time.monotonic()
        self._half_open_probe_at = None
        self._half_open_in_flight = False


_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(service: str) -> CircuitBreaker:
    """Return (and lazily create) the breaker for a configured service."""
    if service not in _breakers:
        settings = get_settings()
        _breakers[service] = CircuitBreaker(
            failure_threshold=settings.breaker_failure_threshold,
            recovery_timeout=settings.breaker_recovery_timeout,
            half_open_timeout=settings.breaker_half_open_timeout,
        )
    return _breakers[service]


def reset_breakers() -> None:
    """Clear the registry. Intended for tests and operational resets."""
    _breakers.clear()


def breaker_snapshot() -> dict[str, dict]:
    return {
        service: {
            "state": breaker.state,
            "failures": breaker.failures,
        }
        for service, breaker in _breakers.items()
    }
