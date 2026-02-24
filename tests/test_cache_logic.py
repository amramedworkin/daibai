"""
L1 cache tests: basic key-value get/set with fakeredis.

Uses fakeredis for standalone tests (no real Redis required).
Live tests (test_*_live) use real Redis when REDIS_URL is set.
"""

import json
import os
from unittest.mock import MagicMock, patch

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


def test_set_semantic_stores_json_structure(cache_manager):
    """set_semantic stores a JSON object with vector and response under semantic: prefix."""
    from daibai.core.cache import SEMANTIC_KEY_PREFIX

    vector = [0.1, 0.2, 0.3]
    text = "What is Azure?"
    response = "Azure is a cloud platform."

    ok = cache_manager.set_semantic(text, response, vector=vector)
    assert ok is True

    client = cache_manager._get_client()
    keys = [k for k in client.keys("*") if k.startswith(SEMANTIC_KEY_PREFIX)]
    assert len(keys) == 1
    assert keys[0].startswith(SEMANTIC_KEY_PREFIX)

    raw = client.get(keys[0])
    payload = json.loads(raw)
    assert payload["text"] == text
    assert payload["vector"] == vector
    assert payload["response"] == response


def test_embedding_generation(cache_manager):
    """get_embedding returns a list of 384 floats for the given text."""
    pytest.importorskip("sentence_transformers")

    result = cache_manager.get_embedding("How do I create a table?")

    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)
    assert len(result) == 384


def test_embedding_model_loaded_once(cache_manager):
    """Embedding model is loaded as singleton to avoid performance lag."""
    from daibai.core.cache import CacheManager

    # Reset singleton so we can observe fresh load
    CacheManager._embed_model = None
    try:
        mock_model = MagicMock()
        mock_encode_result = MagicMock()
        mock_encode_result.tolist.return_value = [0.1] * 384
        mock_model.encode.return_value = mock_encode_result

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ) as mock_st:
            cache_manager.get_embedding("first call")
            cache_manager.get_embedding("second call")
            cache_manager.set_semantic("third", "response")  # also uses embedding

            assert mock_st.call_count == 1, "SentenceTransformer should be instantiated only once"
    finally:
        CacheManager._embed_model = None


def test_set_semantic_auto_generates_vector(cache_manager):
    """set_semantic with no vector auto-generates embedding via get_embedding."""
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import SEMANTIC_KEY_PREFIX

    text = "How many users joined in June?"
    response = "SELECT COUNT(*) FROM users WHERE joined >= '2024-06-01';"

    ok = cache_manager.set_semantic(text, response)
    assert ok is True

    client = cache_manager._get_client()
    keys = [k for k in client.keys("*") if k.startswith(SEMANTIC_KEY_PREFIX)]
    assert len(keys) == 1

    payload = json.loads(client.get(keys[0]))
    assert payload["text"] == text
    assert payload["response"] == response
    assert isinstance(payload["vector"], list)
    assert len(payload["vector"]) == 384
    assert all(isinstance(x, float) for x in payload["vector"])


def test_embedding_graceful_degradation_when_model_fails(cache_manager):
    """When SentenceTransformer fails to load, get_embedding returns None and set_semantic returns False."""
    from daibai.core.cache import CacheManager

    CacheManager._embed_model = None
    try:
        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=RuntimeError("Model download failed"),
        ):
            result = cache_manager.get_embedding("test prompt")
            assert result is None

            ok = cache_manager.set_semantic("prompt", "response")
            assert ok is False
    finally:
        CacheManager._embed_model = None


def test_semantic_hit(cache_manager):
    """Store 'What is the price?' then ask 'What's the price?' — system identifies as same, returns cached answer."""
    pytest.importorskip("sentence_transformers")

    cache_manager.set_semantic("What is the price?", "The price is $10.")
    # Paraphrase with contraction; semantically equivalent, high similarity
    result = cache_manager.check_semantic("What's the price?", threshold=0.90)

    assert result == "The price is $10."


def test_semantic_miss(cache_manager):
    """Store 'What is the price?' then ask 'Who is the CEO?' — system identifies as different, returns None."""
    pytest.importorskip("sentence_transformers")

    cache_manager.set_semantic("What is the price?", "The price is $10.")
    result = cache_manager.check_semantic("Who is the CEO?", threshold=0.70)

    assert result is None


def test_threshold_tuning(cache_manager):
    """With threshold 0.999, a slightly different question returns None (near-perfect match required)."""
    pytest.importorskip("sentence_transformers")

    cache_manager.set_semantic("What is the capital of France?", "Paris.")
    # "Which city is the capital of France?" is similar but typically below 0.999
    result = cache_manager.check_semantic("Which city is the capital of France?", threshold=0.999)

    assert result is None


# --- Live tests (real Redis when REDIS_URL set) ---


def _get_redis_url():
    return (
        os.environ.get("REDIS_URL", "").strip()
        or os.environ.get("AZURE_REDIS_CONNECTION_STRING", "").strip()
    )


@pytest.mark.cloud
@pytest.mark.skipif(
    not _get_redis_url(),
    reason="REDIS_URL or AZURE_REDIS_CONNECTION_STRING not set - live test requires Redis",
)
def test_set_and_get_live():
    """CacheManager set/get against real Redis."""
    from daibai.core.cache import CacheManager

    manager = CacheManager()
    key = "daibai-test:cache_logic:set_get_live"
    value = "live-test-value"
    try:
        manager.set(key, value, ttl=60)
        assert manager.get(key) == value
    finally:
        client = manager._get_client()
        if client:
            client.delete(key)


@pytest.mark.cloud
@pytest.mark.skipif(
    not _get_redis_url(),
    reason="REDIS_URL or AZURE_REDIS_CONNECTION_STRING not set - live test requires Redis",
)
def test_semantic_hit_live():
    """Semantic cache hit against real Redis: store then retrieve similar question."""
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager

    manager = CacheManager()
    try:
        manager.set_semantic("What is the price?", "The price is $10.", ttl=60)
        result = manager.check_semantic("What's the price?", threshold=0.90)
        assert result == "The price is $10."
    finally:
        client = manager._get_client()
        if client:
            for k in client.keys("semantic:*"):
                client.delete(k)


@pytest.mark.cloud
@pytest.mark.skipif(
    not _get_redis_url(),
    reason="REDIS_URL or AZURE_REDIS_CONNECTION_STRING not set - live test requires Redis",
)
def test_semantic_miss_live():
    """Semantic cache miss against real Redis: different questions return None."""
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager

    manager = CacheManager()
    try:
        manager.set_semantic("What is the price?", "The price is $10.", ttl=60)
        result = manager.check_semantic("Who is the CEO?", threshold=0.70)
        assert result is None
    finally:
        client = manager._get_client()
        if client:
            for k in client.keys("semantic:*"):
                client.delete(k)
