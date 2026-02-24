"""
L1 cache tests: basic key-value get/set with fakeredis.

Uses fakeredis for standalone tests (no real Redis required).
"""

import pytest

try:
    import fakeredis
except ImportError:
    fakeredis = None


@pytest.fixture
def fake_redis():
    """Provide a fakeredis FakeRedis instance."""
    if fakeredis is None:
        pytest.skip("fakeredis not installed; pip install fakeredis")
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def cache_manager(fake_redis):
    """CacheManager wired to fakeredis via connection_string to in-memory server."""
    from daibai.core.cache import CacheManager

    # Fakeredis doesn't use URLs; we inject the fake client.
    # CacheManager uses redis.Redis.from_url - fakeredis.FakeRedis is a drop-in.
    manager = CacheManager(connection_string="redis://localhost:6379/0")
    # Replace the lazy client with our fake
    manager._client = fake_redis
    return manager


def test_set_and_get_returns_value(cache_manager):
    """Set a value and get it back (exact match)."""
    cache_manager.set("foo", "bar")
    assert cache_manager.get("foo") == "bar"


def test_get_nonexistent_key_returns_none(cache_manager):
    """A non-existent key returns None."""
    assert cache_manager.get("nonexistent") is None


def test_set_ttl_passed_to_redis(cache_manager):
    """TTL is correctly passed to the set command (ex parameter)."""
    cache_manager.set("ttl_key", "ttl_value", ttl=120)
    # Verify key exists and has TTL set (fakeredis supports ttl())
    ttl = cache_manager._get_client().ttl("ttl_key")
    assert ttl > 0
    assert ttl <= 120
