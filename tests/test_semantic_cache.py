"""
Tests for SemanticCache and CachedLLMProvider.

Uses mocks for Redis and embeddings so tests run without API keys or live Redis.
Live test (test_semantic_cache_live) uses real Redis + real embeddings when REDIS_URL is set.
"""

import os

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# --- Mock Infrastructure ---

@pytest.fixture
def mock_redis():
    """Mock redis.Redis with in-memory storage."""
    storage = {}
    index = set()

    mock = MagicMock()
    mock.ping = MagicMock(return_value=True)
    mock.get = lambda k: storage.get(k)
    mock.set = lambda k, v: storage.update({k: v})
    mock.sadd = lambda k, v: index.add(v) if k == "daibai:semantic_cache:index" else None
    mock.smembers = lambda k: index if k == "daibai:semantic_cache:index" else set()
    mock.close = MagicMock()

    # Expose for test assertions
    mock._storage = storage
    mock._index = index
    return mock


@pytest.fixture
def mock_embedding():
    """Mock embedding function returning deterministic vectors for similarity control."""
    # vec_a and vec_b have cosine similarity ~0.98 (similar prompts)
    vec_a = [1.0] * 64 + [0.0] * 64
    vec_b = [0.98] * 64 + [0.0] * 64  # cos sim with vec_a = 0.98
    vec_c = [0.0] * 64 + [1.0] * 64   # orthogonal to vec_a (sim=0)

    def _embed(text: str):
        text_lower = text.lower()
        if "deploy" in text_lower and "azure" in text_lower and "tell me" in text_lower:
            return vec_b  # "Tell me how to deploy to Azure"
        if "deploy" in text_lower and "azure" in text_lower:
            return vec_a  # "How do I deploy to Azure?"
        if "azure" in text_lower:
            return vec_a
        if "france" in text_lower or "capital" in text_lower:
            return vec_c  # Different context
        return vec_a  # Default

    return _embed


# --- Test Cases ---

def test_exact_match_retrieval(mock_redis, mock_embedding):
    """
    Save a response for "What is Azure?" and retrieve with exact same string.
    Assert returned response matches and LLM generate was not called.
    """
    from daibai.llm.base import SemanticCache, CachedLLMProvider, LLMResponse, BaseLLMProvider

    with patch("redis.from_url", return_value=mock_redis):
        cache = SemanticCache(connection_string="redis://localhost", embed_fn=mock_embedding)
        cache.set_cached_response("What is Azure?", "Azure is a cloud platform.")
        result = cache.get_cached_response("What is Azure?")
        assert result == "Azure is a cloud platform."

    # Integration: CachedLLMProvider should not call underlying LLM on cache hit
    mock_provider = MagicMock(spec=BaseLLMProvider)
    mock_provider.model_name = "test-model"
    mock_provider.generate = MagicMock(return_value=LLMResponse(text="should not be used"))

    cached_provider = CachedLLMProvider(mock_provider, cache)
    response = cached_provider.generate("What is Azure?")

    assert response.text == "Azure is a cloud platform."
    mock_provider.generate.assert_not_called()


def test_semantic_similarity_retrieval(mock_redis, mock_embedding):
    """
    Store for "How do I deploy to Azure?", query with "Tell me how to deploy to Azure".
    Mock embeddings yield cosine similarity 0.98. Assert cache returns stored response.
    """
    from daibai.llm.base import SemanticCache, _cosine_similarity

    # Verify mock yields high similarity
    vec_a = mock_embedding("How do I deploy to Azure?")
    vec_b = mock_embedding("Tell me how to deploy to Azure")
    assert _cosine_similarity(vec_a, vec_b) >= 0.95

    with patch("redis.from_url", return_value=mock_redis):
        cache = SemanticCache(connection_string="redis://localhost", embed_fn=mock_embedding)
        cache.set_cached_response("How do I deploy to Azure?", "Use Azure CLI: az deployment...")
        result = cache.get_cached_response("Tell me how to deploy to Azure")

    assert result == "Use Azure CLI: az deployment..."


def test_cache_miss_different_context(mock_redis, mock_embedding):
    """
    Query "What is the capital of France?" after storing "What is Azure?".
    Assert cache returns None (different context, low similarity).
    """
    from daibai.llm.base import SemanticCache, _cosine_similarity

    vec_azure = mock_embedding("What is Azure?")
    vec_france = mock_embedding("What is the capital of France?")
    assert _cosine_similarity(vec_azure, vec_france) < 0.95

    with patch("redis.from_url", return_value=mock_redis):
        cache = SemanticCache(connection_string="redis://localhost", embed_fn=mock_embedding)
        cache.set_cached_response("What is Azure?", "Azure is a cloud platform.")
        result = cache.get_cached_response("What is the capital of France?")

    assert result is None


def test_resilience_redis_down(mock_embedding):
    """
    Mock redis.exceptions.ConnectionError. Assert system does not crash,
    gracefully falls back (returns None from cache, so LLM is called).
    """
    from redis.exceptions import ConnectionError as RedisConnectionError
    from daibai.llm.base import SemanticCache, CachedLLMProvider, LLMResponse, BaseLLMProvider

    with patch("redis.from_url", side_effect=RedisConnectionError("Connection refused")):
        cache = SemanticCache(connection_string="redis://localhost", embed_fn=mock_embedding)
        result = cache.get_cached_response("What is Azure?")
        assert result is None

    # CachedLLMProvider should fall through to real LLM when cache returns None
    mock_provider = MagicMock(spec=BaseLLMProvider)
    mock_provider.model_name = "test"
    mock_provider.generate = MagicMock(return_value=LLMResponse(text="Fresh LLM response"))

    cached_provider = CachedLLMProvider(mock_provider, cache)
    response = cached_provider.generate("What is Azure?")

    assert response.text == "Fresh LLM response"
    mock_provider.generate.assert_called_once()


def test_integration_with_azure_provider(mock_redis, mock_embedding):
    """
    Instantiate AzureProvider (Azure OpenAI), wrap with CachedLLMProvider.
    Ensure generate() interacts correctly with SemanticCache.
    """
    pytest.importorskip("openai")
    from daibai.llm.base import SemanticCache, CachedLLMProvider, LLMResponse
    from daibai.llm.azure import AzureProvider

    with patch("redis.from_url", return_value=mock_redis):
        cache = SemanticCache(connection_string="redis://localhost", embed_fn=mock_embedding)

        # Create Azure provider with minimal config (will be mocked)
        provider = AzureProvider(
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            deployment="gpt-4",
        )

        with patch.object(provider, "_ensure_client"):
            with patch.object(provider, "generate", return_value=LLMResponse(text="Azure says hi")) as mock_gen:
                cached_provider = CachedLLMProvider(provider, cache)

                # First call: cache miss, calls provider, stores in cache
                r1 = cached_provider.generate("What is Azure?")
                assert r1.text == "Azure says hi"
                assert mock_gen.call_count == 1

                # Second call with same prompt: cache hit, provider NOT called again
                r2 = cached_provider.generate("What is Azure?")
                assert r2.text == "Azure says hi"
                assert mock_gen.call_count == 1  # Still 1, not 2


# --- Live test (real Redis + real embeddings) ---


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
def test_semantic_cache_live():
    """
    SemanticCache against real Redis with real embeddings (sentence-transformers).
    Store "What is Azure?" then retrieve with similar "Tell me about Azure".
    """
    pytest.importorskip("sentence_transformers")

    from daibai.core.config import get_redis_connection_string
    from daibai.llm.base import SemanticCache

    conn = get_redis_connection_string()
    if not conn:
        pytest.skip("No Redis connection string")

    cache = SemanticCache(connection_string=conn, similarity_threshold=0.85)
    try:
        ok = cache.set_cached_response("What is Azure?", "Azure is a cloud platform.")
        assert ok is True
        result = cache.get_cached_response("Tell me about Azure")
        assert result == "Azure is a cloud platform."
    finally:
        cache.close()
