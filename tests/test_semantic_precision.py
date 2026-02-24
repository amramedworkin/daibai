"""
TDD "Broken Promise" test: Semantic cache should return cached answers for paraphrased questions.

This test EXPECTS the system to be smart (semantic matching) but will FAIL because the current
implementation treats "What is the total revenue for 2023?" and "Show me 2023 total revenue."
as different (exact-text or low-similarity behavior). Proves the test suite is working.

Run: python3 -m pytest tests/test_semantic_precision.py -v
Expected: FAIL with "Cache Miss" (assertion that cache returns stored answer fails).
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_redis():
    """Mock redis.Redis with in-memory storage for SemanticCache (set/get + sadd/smembers)."""
    storage = {}
    index = set()

    mock = MagicMock()
    mock.ping = MagicMock(return_value=True)
    mock.get = lambda k: storage.get(k)
    mock.set = lambda k, v: storage.update({k: v})
    mock.sadd = lambda k, v: index.add(v) if k == "daibai:semantic_cache:index" else None
    mock.smembers = lambda k: index if k == "daibai:semantic_cache:index" else set()
    mock.close = MagicMock()
    mock._storage = storage
    mock._index = index
    return mock


@pytest.fixture
def exact_match_only_embedding():
    """
    Simulates 'exact text match only' behavior: same vector only for identical strings.
    Paraphrases get orthogonal vectors -> cosine sim 0 -> Cache Miss.
    """
    vec_stored = [1.0] * 64 + [0.0] * 64
    vec_paraphrase = [0.0] * 64 + [1.0] * 64  # orthogonal, sim=0

    def _embed(text: str):
        # Exact stored prompt gets vec_stored; anything else gets vec_paraphrase
        if text.strip() == "What is the total revenue for 2023?":
            return vec_stored
        return vec_paraphrase

    return _embed


@pytest.mark.xfail(
    reason="TDD: Paraphrased questions should hit cache; currently exact-match only. Expected failure = success.",
    strict=False,
)
def test_paraphrased_question_should_hit_cache(mock_redis, exact_match_only_embedding):
    """
    TDD Broken Promise: Store answer for "What is the total revenue for 2023?",
    retrieve with "Show me 2023 total revenue." — EXPECT cache hit.
    FAILS because current behavior is exact-match (paraphrase -> Cache Miss).
    """
    from daibai.llm.base import SemanticCache

    stored_prompt = "What is the total revenue for 2023?"
    stored_answer = "SELECT SUM(amount) FROM revenue WHERE year = 2023;"

    with patch("redis.from_url", return_value=mock_redis):
        cache = SemanticCache(
            connection_string="redis://localhost",
            similarity_threshold=0.90,
            embed_fn=exact_match_only_embedding,
        )
        cache.set_cached_response(stored_prompt, stored_answer)

        # Paraphrased question — semantically equivalent, should hit cache
        result = cache.get_cached_response("Show me 2023 total revenue.")

    # This assertion FAILS (Cache Miss) until semantic matching is fixed
    assert result == stored_answer, (
        "Cache Miss: Paraphrased question should return cached answer. "
        "System is still using exact-text matching instead of semantic similarity."
    )
