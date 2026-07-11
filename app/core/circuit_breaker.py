import time

class CircuitBreaker:
    """
    A state machine that prevents the gateway from continuously attempting 
    to send traffic to a backend service that is currently down or timing out.
    """
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30):
        # How many consecutive failures before the breaker trips
        self.failure_threshold = failure_threshold
        # How many seconds to wait before testing if the backend is back online
        self.recovery_timeout = recovery_timeout
        
        self.failures = 0
        self.state = "CLOSED"  # States: CLOSED (healthy) | OPEN (failing) | HALF_OPEN (testing)
        self.opened_at = None

    def record_success(self):
        """
        Called when a request succeeds. Resets the failure count and 
        ensures the circuit is fully CLOSED.
        """
        self.failures = 0
        self.state = "CLOSED"

    def record_failure(self):
        """
        Called when a request fails (e.g., timeout or 5xx error). 
        Trips the breaker to OPEN if the threshold is reached.
        """
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = time.time()

    def allow_request(self) -> bool:
        """
        Determines if a request should be allowed through to the backend.
        """
        if self.state == "OPEN":
            # Check if enough time has passed to try a test request
            if time.time() - self.opened_at > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            # The recovery timeout hasn't elapsed; block the request
            return False
            
        # If CLOSED or HALF_OPEN, allow the request to proceed
        return True

# Global registry of circuit breakers (one per downstream service)
breakers = {
    "users": CircuitBreaker(failure_threshold=5, recovery_timeout=30),
    "orders": CircuitBreaker(failure_threshold=5, recovery_timeout=30),
}