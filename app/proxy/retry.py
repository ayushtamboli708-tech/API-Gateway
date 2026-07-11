import asyncio
from fastapi import Request, Response, HTTPException
from app.core.circuit_breaker import breakers
from app.proxy.forwarder import forward_request

# Only safe, non-mutating methods should be retried automatically
IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}

async def forward_with_resilience(service: str, path: str, request: Request) -> Response:
    """
    Wraps the proxy forwarder with Circuit Breaking and Exponential Backoff Retries.
    """
    breaker = breakers.get(service)
    
    # 1. Circuit Breaker Check: Block immediately if the backend is down (OPEN)
    if breaker and not breaker.allow_request():
        raise HTTPException(
            status_code=503, 
            detail="Service temporarily unavailable (Circuit Open)"
        )

    # 2. Retry Configuration
    # Allow 2 retries (3 total attempts) for idempotent methods. 
    # Mutations (POST/PUT/DELETE) get 0 retries to prevent duplicate data creation.
    max_retries = 2 if request.method.upper() in IDEMPOTENT_METHODS else 0
    delay = 0.5  # Start with a 500ms delay

    # 3. Execution Loop
    for attempt in range(max_retries + 1):
        try:
            # Attempt to forward the request downstream
            response = await forward_request(service, path, request)
            
            # If successful, record it so the circuit breaker knows the service is healthy
            if breaker:
                breaker.record_success()
            
            return response
            
        except HTTPException as e:
            # We only want to retry on Gateway Timeouts (504) or Bad Gateways (502).
            # We should NOT retry on client errors (4xx) or backend internal crashes (500).
            if e.status_code not in (502, 504):
                raise 

            # Record the failure to potentially trip the circuit breaker
            if breaker:
                breaker.record_failure()
            
            # If we have exhausted all attempts, pass the error back to the client
            if attempt == max_retries:
                raise
            
            # Exponential Backoff: Wait, then double the delay for the next attempt (0.5s -> 1.0s)
            await asyncio.sleep(delay)
            delay *= 2