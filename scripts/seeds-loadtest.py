import asyncio
from app.core.redis_client import redis_client

async def seed_load_test_tenant():
    """
    Injects a 'god-mode' tenant into Redis for stress-testing 
    proxy throughput, network latency, and circuit breakers.
    """
    tenant_id = "tenant_loadtest"
    api_key = "key-loadtest-999"
    
    tenant_data = {
        "tenant_id": tenant_id,
        "plan": "benchmark",
        "monthly_quota": 10_000_000,    # Prevents 403s
        "rate_limit_per_min": 100_000,  # Prevents 429s
    }
    
    await redis_client.hset(f"tenant:{api_key}", mapping=tenant_data)
    print(f"✅ Seeded {tenant_id} with API Key '{api_key}' for maximum throughput.")

if __name__ == "__main__":
    asyncio.run(seed_load_test_tenant())