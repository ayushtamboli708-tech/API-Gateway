import asyncio
from app.core.redis_client import redis_client

async def seed():
    """
    Seeds initial tenant data into Redis for local testing.
    """
    await redis_client.hset(
        "tenant:key-abc123", 
        mapping={
            "tenant_id": "tenant_1",
            "plan": "pro",
            "monthly_quota": 100000,
            "rate_limit_per_min": 60,
        }
    )
    print("✅ Seeded tenant_1 with API Key 'key-abc123'")

if __name__ == "__main__":
    asyncio.run(seed())
