"""
Seed a development tenant into Redis.

Run from the repository root with either:
    python -m scripts.seed_tenants
    python scripts/seed_tenants.py
"""

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.exceptions  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core import redis_client as redis_store  # noqa: E402


async def seed() -> None:
    await redis_store.redis_client.hset(
        "tenant:key-abc123",
        mapping={
            "tenant_id": "tenant_1",
            "plan": "pro",
            "monthly_quota": 10000,
            "rate_limit_per_min": 60,
        },
    )
    print("[OK] Seeded tenant_1 with API Key 'key-abc123'")


async def main() -> None:
    try:
        await seed()
    except redis.exceptions.RedisError:
        print(
            f"[ERROR] Could not connect to Redis at {get_settings().redis_url}. "
            "Start it with `docker compose up -d redis`."
        )
        raise SystemExit(1) from None
    finally:
        await redis_store.close_redis()


if __name__ == "__main__":
    asyncio.run(main())
