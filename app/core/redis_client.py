import redis.asyncio as redis

from app.config import get_settings

_settings = get_settings()

# A single async Redis client, configured with strict socket timeouts so a
# degraded Redis instance cannot hang gateway requests indefinitely.
redis_client: redis.Redis = redis.from_url(
    _settings.redis_url,
    decode_responses=True,
    socket_timeout=_settings.redis_socket_timeout,
    socket_connect_timeout=_settings.redis_connect_timeout,
)


async def close_redis() -> None:
    """Close the Redis connection pool. Called from the FastAPI lifespan."""
    await redis_client.aclose()
