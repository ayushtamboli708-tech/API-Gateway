import fakeredis.aioredis
import pytest

from app.core import redis_client as redis_store
from app.core.circuit_breaker import reset_breakers


@pytest.fixture(autouse=True)
def reset_breaker_registry():
    reset_breakers()
    yield
    reset_breakers()


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_store, "redis_client", client)
    return client
