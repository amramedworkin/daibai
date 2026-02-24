"""
Integration tests for Azure Cache for Redis.

Validates add, retrieve, delete, and bad-key retrieval against live Redis.
Requires REDIS_URL (rediss://:password@hostname:port for Azure).
"""

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.cloud,
    pytest.mark.skipif(
        not os.environ.get("REDIS_URL", "").strip(),
        reason="REDIS_URL not set - Redis test requires live Azure Cache for Redis",
    ),
]


def _test_key(prefix="daibai-test"):
    """Unique key to avoid collisions."""
    return f"{prefix}:{uuid.uuid4().hex}"


@pytest.fixture
def redis_client():
    """Create Redis client, yield, then close."""
    import redis

    url = os.environ["REDIS_URL"].strip()
    client = redis.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


def test_redis_add_and_retrieve_key(redis_client):
    """
    Add a key-value pair and retrieve it successfully.
    """
    key = _test_key("add-retrieve")
    value = "hello-redis"
    redis_client.set(key, value)
    try:
        retrieved = redis_client.get(key)
        assert retrieved == value
    finally:
        redis_client.delete(key)


def test_redis_retrieve_multiple_keys(redis_client):
    """
    Add multiple keys and retrieve them.
    """
    keys = [_test_key("multi") for _ in range(3)]
    values = ["v1", "v2", "v3"]
    for k, v in zip(keys, values):
        redis_client.set(k, v)
    try:
        for k, v in zip(keys, values):
            assert redis_client.get(k) == v
    finally:
        for k in keys:
            redis_client.delete(k)


def test_redis_delete_key(redis_client):
    """
    Add a key, delete it, then verify it is gone.
    """
    key = _test_key("delete")
    redis_client.set(key, "to-delete")
    redis_client.delete(key)
    retrieved = redis_client.get(key)
    assert retrieved is None


def test_redis_retrieve_nonexistent_key(redis_client):
    """
    Retrieve a key that does not exist returns None.
    """
    key = _test_key("nonexistent")
    # Ensure it does not exist
    redis_client.delete(key)
    retrieved = redis_client.get(key)
    assert retrieved is None


def test_redis_retrieve_after_delete(redis_client):
    """
    Add key, delete it, then get returns None (bad key retrieval).
    """
    key = _test_key("after-delete")
    redis_client.set(key, "will-be-deleted")
    redis_client.delete(key)
    bad_retrieve = redis_client.get(key)
    assert bad_retrieve is None


def test_redis_full_lifecycle(redis_client):
    """
    Full lifecycle: add keys, retrieve, delete, verify bad retrieval.
    """
    key1 = _test_key("lifecycle")
    key2 = _test_key("lifecycle")
    redis_client.set(key1, "val1")
    redis_client.set(key2, "val2")
    assert redis_client.get(key1) == "val1"
    assert redis_client.get(key2) == "val2"
    redis_client.delete(key1)
    assert redis_client.get(key1) is None
    assert redis_client.get(key2) == "val2"
    redis_client.delete(key2)
    assert redis_client.get(key2) is None
