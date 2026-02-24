"""
Tests for semantic schema mapping (table pruning).

Verifies that get_relevant_tables() returns only tables relevant to the query,
e.g. "salaries" returns Employees DDL and excludes WeatherForecast.
Live test (test_schema_mapping_live) uses real Redis + real embeddings when REDIS_URL set.
"""

import json
import os

import pytest

from daibai.core.schema import SchemaManager, SCHEMA_VECTOR_PREFIX


# Mock metadata: Employees (salary-related) and WeatherForecast (unrelated)
_MOCK_METADATA = {
    "Employees": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "name", "data_type": "varchar", "column_type": "varchar(255)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
        {"column_name": "salary", "data_type": "decimal", "column_type": "decimal(10,2)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "WeatherForecast": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "city", "data_type": "varchar", "column_type": "varchar(100)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
        {"column_name": "temperature", "data_type": "int", "column_type": "int", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
}


def _mock_execute(sql: str, params: tuple = ()):
    """Return rows mimicking information_schema.COLUMNS for Employees and WeatherForecast."""
    rows = []
    for table, cols in _MOCK_METADATA.items():
        for col in cols:
            rows.append({
                "TABLE_SCHEMA": "testdb",
                "TABLE_NAME": table,
                "COLUMN_NAME": col["column_name"],
                "DATA_TYPE": col["data_type"],
                "COLUMN_TYPE": col["column_type"],
                "IS_NULLABLE": "YES" if col["is_nullable"] else "NO",
                "COLUMN_KEY": col["column_key"],
                "COLUMN_DEFAULT": col["column_default"],
                "EXTRA": col["extra"],
            })
    return rows


@pytest.fixture
def mock_redis():
    """In-memory Redis-like store for schema vectors."""
    storage = {}
    index = set()

    class MockRedis:
        def get(self, k):
            return storage.get(k)

        def set(self, k, v, ex=None):
            storage[k] = v

        def sadd(self, name, member):
            index.add(member)

        def smembers(self, name):
            return index

    client = MockRedis()
    client._storage = storage
    client._index = index
    return client


@pytest.fixture
def mock_embedding():
    """
    Embedding that returns vectors by semantic relevance.
    'salaries' / 'salary' / 'employee' -> vec_A (similar to Employees).
    'weather' / 'forecast' / 'temperature' -> vec_B (similar to WeatherForecast).
    vec_A and vec_B are orthogonal.
    """
    vec_salary = [1.0] * 64 + [0.0] * 64
    vec_weather = [0.0] * 64 + [1.0] * 64

    def _embed(text: str):
        t = text.lower()
        if "salary" in t or "salaries" in t or "employee" in t or "employees" in t:
            return vec_salary
        if "weather" in t or "forecast" in t or "temperature" in t or "city" in t:
            return vec_weather
        return vec_salary  # default

    return _embed


@pytest.fixture
def schema_manager(mock_redis, mock_embedding):
    """SchemaManager with mocked DB, Redis, and embeddings."""
    return SchemaManager(
        execute_fn=_mock_execute,
        redis_client=mock_redis,
        embed_fn=mock_embedding,
    )


def test_salaries_query_returns_employees_excludes_weatherforecast(schema_manager):
    """
    Query about 'salaries' returns Employees DDL and excludes WeatherForecast.
    Confirms semantic schema pruning works.
    """
    schema_manager.vectorize_schema("testdb")
    result = schema_manager.get_relevant_tables("What are the employee salaries?", "testdb", limit=5)

    ddl_text = "\n".join(result)
    assert "Employees" in ddl_text
    assert "salary" in ddl_text
    assert "WeatherForecast" not in ddl_text
    assert "temperature" not in ddl_text


def test_weather_query_returns_weatherforecast_excludes_employees(schema_manager):
    """Query about weather returns WeatherForecast and excludes Employees."""
    schema_manager.vectorize_schema("testdb")
    result = schema_manager.get_relevant_tables("Show me the weather forecast for cities", "testdb", limit=5)

    ddl_text = "\n".join(result)
    assert "WeatherForecast" in ddl_text
    assert "temperature" in ddl_text
    assert "Employees" not in ddl_text
    assert "salary" not in ddl_text


def test_vectorize_schema_stores_in_redis(schema_manager, mock_redis):
    """vectorize_schema stores table DDLs with embeddings in Redis."""
    count = schema_manager.vectorize_schema("testdb")
    assert count == 2

    assert "Employees" in mock_redis._index
    assert "WeatherForecast" in mock_redis._index

    key = f"{SCHEMA_VECTOR_PREFIX}testdb:Employees"
    raw = mock_redis.get(key)
    assert raw is not None
    payload = json.loads(raw)
    assert "ddl" in payload
    assert "vector" in payload
    assert "salary" in payload["ddl"]


def test_get_relevant_tables_respects_limit(schema_manager):
    """get_relevant_tables returns at most limit tables."""
    schema_manager.vectorize_schema("testdb")
    result = schema_manager.get_relevant_tables("salaries", "testdb", limit=1)
    assert len(result) <= 1


def test_get_relevant_tables_empty_without_vectorize(schema_manager):
    """get_relevant_tables returns empty list if no schema vectorized."""
    result = schema_manager.get_relevant_tables("salaries", "testdb")
    assert result == []


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
def test_schema_mapping_live():
    """
    vectorize_schema + get_relevant_tables against real Redis with real embeddings.
    Mock DB (Employees, WeatherForecast); real Redis + sentence-transformers.
    Verifies "salaries" returns Employees and excludes WeatherForecast.
    """
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager
    from daibai.core.config import get_redis_connection_string

    conn = get_redis_connection_string()
    if not conn:
        pytest.skip("No Redis connection string")

    cache = CacheManager(connection_string=conn)
    manager = SchemaManager(
        execute_fn=_mock_execute,
        cache_manager=cache,
    )
    try:
        count = manager.vectorize_schema("testdb")
        assert count == 2

        result = manager.get_relevant_tables("What are the employee salaries?", "testdb", limit=5)
        ddl_text = "\n".join(result)
        assert "Employees" in ddl_text
        assert "salary" in ddl_text
        assert "WeatherForecast" not in ddl_text
    finally:
        # Clean up schema:testdb:* keys
        client = cache._get_client()
        if client:
            for key in client.keys(f"{SCHEMA_VECTOR_PREFIX}testdb:*"):
                client.delete(key)
            client.delete(f"{SCHEMA_VECTOR_PREFIX}testdb:index")
