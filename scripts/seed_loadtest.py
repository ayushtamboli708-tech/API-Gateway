"""
Seed a high-limit tenant used by the Locust load test.

Run from the repository root with either:
    python -m scripts.seed_loadtest
    python scripts/seed_loadtest.py
"""

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.exceptions  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core import redis_client as redis_store  # noqa: E402


async def seed_load_test_tenant() -> None:
    tenant_id = "tenant_loadtest"
    api_key = "key-loadtest-999"

    await redis_store.redis_client.hset(
        f"tenant:{api_key}",
        mapping={
            "tenant_id": tenant_id,
            "plan": "benchmark",
            "monthly_quota": 10_000_000,
            "rate_limit_per_min": 100_000,
        },
    )
    print(f"[OK] Seeded {tenant_id} with API Key '{api_key}' for maximum throughput.")


async def main() -> None:
    try:
        await seed_load_test_tenant()
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
