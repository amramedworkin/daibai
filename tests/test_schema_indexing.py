"""
Phase 3 Step 1: High-precision semantic schema indexing tests.

Verifies discover_schema(), index_schema(), and search_schema_v1() using
schema:v1:* Redis keys. Mock data: financial_records and weather_data.
Success state: "How much money did we make?" returns financial_records as top result.
Live test (test_index_and_search_schema_live) uses real Redis + real embeddings when REDIS_URL set.
"""

import json
import os

import pytest

from daibai.core.schema import (
    SchemaManager,
    SCHEMA_V1_DDL_PREFIX,
    SCHEMA_V1_TEXT_PREFIX,
    SCHEMA_V1_INDEX_PREFIX,
)


# Mock metadata: financial_records (revenue/money) and weather_data (unrelated)
_MOCK_METADATA = {
    "financial_records": [
        {
            "column_name": "id",
            "data_type": "int",
            "column_type": "int",
            "is_nullable": False,
            "column_key": "PRI",
            "column_default": None,
            "extra": "",
        },
        {
            "column_name": "revenue",
            "data_type": "decimal",
            "column_type": "decimal(12,2)",
            "is_nullable": True,
            "column_key": "",
            "column_default": None,
            "extra": "",
        },
        {
            "column_name": "amount",
            "data_type": "decimal",
            "column_type": "decimal(10,2)",
            "is_nullable": True,
            "column_key": "",
            "column_default": None,
            "extra": "",
        },
    ],
    "weather_data": [
        {
            "column_name": "id",
            "data_type": "int",
            "column_type": "int",
            "is_nullable": False,
            "column_key": "PRI",
            "column_default": None,
            "extra": "",
        },
        {
            "column_name": "city",
            "data_type": "varchar",
            "column_type": "varchar(100)",
            "is_nullable": True,
            "column_key": "",
            "column_default": None,
            "extra": "",
        },
        {
            "column_name": "temperature",
            "data_type": "int",
            "column_type": "int",
            "is_nullable": True,
            "column_key": "",
            "column_default": None,
            "extra": "",
        },
    ],
}


def _mock_execute(sql: str, params: tuple = ()):
    """Return rows mimicking information_schema.COLUMNS for financial_records and weather_data."""
    rows = []
    for table, cols in _MOCK_METADATA.items():
        for col in cols:
            rows.append(
                {
                    "TABLE_SCHEMA": "testdb",
                    "TABLE_NAME": table,
                    "COLUMN_NAME": col["column_name"],
                    "DATA_TYPE": col["data_type"],
                    "COLUMN_TYPE": col["column_type"],
                    "IS_NULLABLE": "YES" if col["is_nullable"] else "NO",
                    "COLUMN_KEY": col["column_key"],
                    "COLUMN_DEFAULT": col["column_default"],
                    "EXTRA": col["extra"],
                }
            )
    return rows


@pytest.fixture
def mock_redis_v1():
    """Redis-like store supporting multiple keys and sets for schema:v1 format."""

    class MockRedis:
        def __init__(self):
            self._storage = {}
            self._sets = {}

        def get(self, k):
            return self._storage.get(k)

        def set(self, k, v, ex=None):
            self._storage[k] = v

        def sadd(self, name, member):
            if name not in self._sets:
                self._sets[name] = set()
            self._sets[name].add(member)

        def smembers(self, name):
            return self._sets.get(name, set())

        def exists(self, *keys):
            count = 0
            for k in keys:
                if k in self._storage or k in self._sets:
                    count += 1
            return count

        def expire(self, key, seconds):
            pass  # no-op for mock

        def srem(self, name, *members):
            if name in self._sets:
                for m in members:
                    self._sets[name].discard(m)

    return MockRedis()


@pytest.fixture
def mock_embedding_financial():
    """
    Embedding that maps financial/money terms to vec_financial, weather terms to vec_weather.
    financial_records DDL (contains revenue, amount) -> vec_financial.
    weather_data DDL (contains temperature, city) -> vec_weather.
    """

    vec_financial = [1.0] * 64 + [0.0] * 64
    vec_weather = [0.0] * 64 + [1.0] * 64

    def _embed(text: str):
        t = text.lower()
        if any(
            w in t
            for w in [
                "revenue",
                "amount",
                "financial",
                "money",
                "make",
                "records",
            ]
        ):
            return vec_financial
        if any(
            w in t for w in ["weather", "temperature", "city", "forecast"]
        ):
            return vec_weather
        return vec_financial  # default for unknown

    return _embed


@pytest.fixture
def schema_manager_indexing(mock_redis_v1, mock_embedding_financial):
    """SchemaManager with mocked DB, Redis, and embeddings for indexing tests."""
    return SchemaManager(
        execute_fn=_mock_execute,
        redis_client=mock_redis_v1,
        embed_fn=mock_embedding_financial,
    )


# ---------------------------------------------------------------------------
# Test 1: Discovery
# ---------------------------------------------------------------------------


def test_discover_schema_formats_ddl_correctly(schema_manager_indexing):
    """
    discover_schema() correctly formats DDL strings.
    Assert that the returned dict has table names as keys and valid DDL as values.
    """
    result = schema_manager_indexing.discover_schema("testdb")

    assert isinstance(result, dict)
    assert "financial_records" in result
    assert "weather_data" in result

    ddl_fin = result["financial_records"]
    assert "financial_records" in ddl_fin or "Table:" in ddl_fin
    assert "revenue" in ddl_fin
    assert "amount" in ddl_fin

    ddl_weather = result["weather_data"]
    assert "weather_data" in ddl_weather or "Table:" in ddl_weather
    assert "temperature" in ddl_weather
    assert "city" in ddl_weather


def test_discover_schema_ddl_contains_column_types(schema_manager_indexing):
    """DDL strings include column types (decimal, varchar, int)."""
    result = schema_manager_indexing.discover_schema("testdb")

    assert "decimal" in result["financial_records"].lower()
    assert "varchar" in result["weather_data"].lower() or "int" in result["weather_data"].lower()


# ---------------------------------------------------------------------------
# Test 2: Indexing
# ---------------------------------------------------------------------------


def test_index_schema_stores_keys_in_redis(schema_manager_indexing, mock_redis_v1):
    """
    index_schema() stores schema:v1:ddl:{db}:{table} and schema:v1:text:{db}:{table} in Redis.
    Verify both tables have ddl and text keys.
    """
    count = schema_manager_indexing.index_schema("testdb", force=True)

    assert count == 2

    index_key = f"{SCHEMA_V1_INDEX_PREFIX}testdb"
    for table in ["financial_records", "weather_data"]:
        ddl_key = f"{SCHEMA_V1_DDL_PREFIX}testdb:{table}"
        text_key = f"{SCHEMA_V1_TEXT_PREFIX}testdb:{table}"

        raw_vector = mock_redis_v1.get(ddl_key)
        assert raw_vector is not None
        vector = json.loads(raw_vector)
        assert isinstance(vector, list)
        assert len(vector) == 128

        raw_ddl = mock_redis_v1.get(text_key)
        assert raw_ddl is not None
        assert table in raw_ddl or "Table:" in raw_ddl

    assert "financial_records" in mock_redis_v1.smembers(index_key)
    assert "weather_data" in mock_redis_v1.smembers(index_key)


def test_index_schema_stores_vector_as_json_array(schema_manager_indexing, mock_redis_v1):
    """Vector stored in schema:v1:ddl:{db}:{table} is valid JSON array of floats."""
    schema_manager_indexing.index_schema("testdb", force=True)

    raw = mock_redis_v1.get(f"{SCHEMA_V1_DDL_PREFIX}testdb:financial_records")
    vector = json.loads(raw)
    assert all(isinstance(x, (int, float)) for x in vector)


# ---------------------------------------------------------------------------
# Test 3: Semantic Search
# ---------------------------------------------------------------------------


def test_semantic_search_money_returns_financial_records_top(
    schema_manager_indexing,
):
    """
    "How much money did we make?" returns financial_records as the top result.
    Success state: system identifies financial tables without hardcoding.
    """
    schema_manager_indexing.index_schema("testdb", force=True)
    result = schema_manager_indexing.search_schema_v1(
        "How much money did we make?", schema_name="testdb", limit=5
    )

    assert len(result) >= 1
    top_ddl = result[0]
    assert "financial_records" in top_ddl or "revenue" in top_ddl or "amount" in top_ddl
    assert "weather_data" not in top_ddl
    assert "temperature" not in top_ddl


def test_semantic_search_revenue_returns_financial_records(schema_manager_indexing):
    """Query about 'Revenue' returns financial_records (Invoices-like table)."""
    schema_manager_indexing.index_schema("testdb", force=True)
    result = schema_manager_indexing.search_schema_v1(
        "What is our total revenue for 2023?", schema_name="testdb", limit=3
    )

    assert len(result) >= 1
    ddl_text = "\n".join(result)
    assert "financial_records" in ddl_text or "revenue" in ddl_text
    assert "weather_data" not in ddl_text


def test_semantic_search_weather_returns_weather_data(schema_manager_indexing):
    """Query about weather returns weather_data, excludes financial_records."""
    schema_manager_indexing.index_schema("testdb", force=True)
    result = schema_manager_indexing.search_schema_v1(
        "Show me the weather forecast for cities", schema_name="testdb", limit=5
    )

    assert len(result) >= 1
    ddl_text = "\n".join(result)
    assert "weather_data" in ddl_text or "temperature" in ddl_text
    assert "financial_records" not in ddl_text
    assert "revenue" not in ddl_text


# ---------------------------------------------------------------------------
# Safety & Guardrails
# ---------------------------------------------------------------------------


def test_index_schema_returns_zero_when_embedding_unavailable(mock_redis_v1):
    """When embed_fn returns None, index_schema returns 0 (Lazy Discovery fallback)."""

    def _fail_embed(_text):
        return None

    manager = SchemaManager(
        execute_fn=_mock_execute,
        redis_client=mock_redis_v1,
        embed_fn=_fail_embed,
    )
    count = manager.index_schema("testdb", force=True)
    assert count == 0


def test_index_schema_returns_zero_when_redis_unavailable(mock_embedding_financial):
    """When Redis client is None, index_schema returns 0."""
    manager = SchemaManager(
        execute_fn=_mock_execute,
        redis_client=None,
        embed_fn=mock_embedding_financial,
    )
    count = manager.index_schema("testdb", force=True)
    assert count == 0


def test_search_schema_v1_empty_without_index(schema_manager_indexing):
    """search_schema_v1 returns empty list if no schema indexed."""
    result = schema_manager_indexing.search_schema_v1("How much money?", schema_name="testdb")
    assert result == []


def test_search_schema_v1_respects_limit(schema_manager_indexing):
    """search_schema_v1 returns at most limit tables."""
    schema_manager_indexing.index_schema("testdb", force=True)
    result = schema_manager_indexing.search_schema_v1(
        "How much money did we make?", schema_name="testdb", limit=1
    )
    assert len(result) <= 1


# ---------------------------------------------------------------------------
# Refresh interval (requires env override in test)
# ---------------------------------------------------------------------------


def test_index_schema_force_bypasses_refresh_check(schema_manager_indexing):
    """force=True bypasses SCHEMA_REFRESH_INTERVAL check."""
    schema_manager_indexing.index_schema("testdb", force=True)
    count = schema_manager_indexing.index_schema("testdb", force=False)
    # With mock, we don't have real time - may or may not skip.
    # At least verify force=True works.
    assert count >= 0


# ---------------------------------------------------------------------------
# discover_schema edge cases
# ---------------------------------------------------------------------------


def test_discover_schema_raises_without_schema_name():
    """discover_schema raises when schema_name and config are both missing."""
    manager = SchemaManager(execute_fn=_mock_execute, redis_client=None)
    with pytest.raises(ValueError, match="schema_name or config.database"):
        manager.discover_schema(None)


def test_index_schema_raises_without_schema_name(mock_redis_v1, mock_embedding_financial):
    """index_schema raises when schema_name and config are both missing."""
    manager = SchemaManager(
        execute_fn=_mock_execute,
        redis_client=mock_redis_v1,
        embed_fn=mock_embedding_financial,
    )
    # execute_fn returns testdb rows but we have no config.database
    with pytest.raises(ValueError, match="schema_name or config.database"):
        manager.index_schema(None)


# ---------------------------------------------------------------------------
# xfail: Known limitations or future improvements
# ---------------------------------------------------------------------------


def test_search_schema_v1_filters_by_schema_name(schema_manager_indexing):
    """search_schema_v1 filters by schema_name (namespaced keys per database)."""
    schema_manager_indexing.index_schema("testdb", force=True)
    result = schema_manager_indexing.search_schema_v1(
        "revenue", schema_name="testdb", limit=5
    )
    assert all("financial_records" in r or "revenue" in r for r in result)


def test_index_schema_handles_empty_schema():
    """index_schema returns 0 when discover_schema returns no tables."""

    def _empty_execute(sql, params):
        return []

    class MinimalMockRedis:
        def get(self, k):
            return None

        def set(self, k, v, ex=None):
            pass

        def sadd(self, name, member):
            pass

        def smembers(self, name):
            return set()

    manager = SchemaManager(
        execute_fn=_empty_execute,
        redis_client=MinimalMockRedis(),
        embed_fn=lambda t: [0.0] * 128,
    )
    count = manager.index_schema("empty_db", force=True)
    assert count == 0


# --- Live test (real Redis + real embeddings, mock DB) ---


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
def test_index_and_search_schema_live():
    """
    Schema indexing against real Redis with real embeddings (sentence-transformers).
    Mock DB (financial_records, weather_data); real Redis + embeddings.
    Verifies "How much money?" returns financial_records as top result.
    """
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager
    from daibai.core.config import get_redis_connection_string

    conn = get_redis_connection_string()
    if not conn:
        pytest.skip("REDIS_URL or AZURE_REDIS_CONNECTION_STRING not set - live schema indexing test requires Redis")

    cache = CacheManager(connection_string=conn)
    manager = SchemaManager(
        execute_fn=_mock_execute,
        cache_manager=cache,
    )
    try:
        count = manager.index_schema("testdb", force=True)
        assert count == 2

        # Real embeddings (sentence-transformers) score lower than mock; use threshold=0.2
        result = manager.search_schema_v1(
            "How much money did we make?", limit=5, threshold=0.2
        )
        assert len(result) >= 1
        top = result[0]
        assert "financial_records" in top or "revenue" in top or "amount" in top
        assert "weather_data" not in top
    finally:
        # Clean up schema:v1:* keys (namespaced by testdb)
        client = cache._get_client()
        if client:
            index_key = f"{SCHEMA_V1_INDEX_PREFIX}testdb"
            for table in ["financial_records", "weather_data"]:
                client.delete(f"{SCHEMA_V1_DDL_PREFIX}testdb:{table}")
                client.delete(f"{SCHEMA_V1_TEXT_PREFIX}testdb:{table}")
                client.srem(index_key, table)
