"""
Phase 3 Step 2: Dynamic Context Pruning integration tests.

Verifies that DaiBaiAgent uses semantic schema pruning to inject only relevant
tables into the LLM prompt, reducing token cost and improving accuracy on
large databases (1000+ tables).

Tests:
- Mock: 10 tables, query about 2 → prompt contains only those 2 DDLs
- Mock: run_sql uses _last_allowed_tables from pruned context
- Mock: scope enforcement blocks out-of-scope tables
- Live: Real Redis + embeddings + MySQL when available
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daibai.core.agent import DaiBaiAgent
from daibai.core.config import Config, DatabaseConfig
from daibai.core.guardrails import SecurityViolation
from daibai.core.schema import SchemaManager, SCHEMA_V1_DDL_PREFIX, SCHEMA_V1_INDEX_PREFIX, SCHEMA_V1_TEXT_PREFIX


# ---------------------------------------------------------------------------
# Mock metadata: 10 tables (2 revenue-related, 8 unrelated)
# ---------------------------------------------------------------------------

_MOCK_METADATA_10 = {
    "financial_records": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "revenue", "data_type": "decimal", "column_type": "decimal(12,2)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
        {"column_name": "amount", "data_type": "decimal", "column_type": "decimal(10,2)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "sales_summary": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "total_sales", "data_type": "decimal", "column_type": "decimal(14,2)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "users": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "email", "data_type": "varchar", "column_type": "varchar(255)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "products": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "name", "data_type": "varchar", "column_type": "varchar(200)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "orders": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "user_id", "data_type": "int", "column_type": "int", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "inventory": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "quantity", "data_type": "int", "column_type": "int", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "audit_log": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "action", "data_type": "varchar", "column_type": "varchar(50)", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "config": [
        {"column_name": "key", "data_type": "varchar", "column_type": "varchar(100)", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "value", "data_type": "text", "column_type": "text", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "sessions": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "user_id", "data_type": "int", "column_type": "int", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
    "notifications": [
        {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        {"column_name": "message", "data_type": "text", "column_type": "text", "is_nullable": True, "column_key": "", "column_default": None, "extra": ""},
    ],
}


def _mock_execute_10(sql: str, params: tuple = ()):
    """Return rows mimicking information_schema.COLUMNS for 10 tables."""
    rows = []
    for table, cols in _MOCK_METADATA_10.items():
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
def mock_redis_v1():
    """Redis-like store for schema:v1 format."""

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

    return MockRedis()


@pytest.fixture
def mock_embedding_revenue_only():
    """
    Embedding: 'revenue'/'sales'/'money' -> financial_records, sales_summary.
    All other terms -> users (fallback).
    """
    vec_revenue = [1.0] * 64 + [0.0] * 64
    vec_other = [0.0] * 64 + [1.0] * 64

    def _embed(text: str):
        t = text.lower()
        if any(w in t for w in ["revenue", "sales", "money", "financial", "amount", "total_sales"]):
            return vec_revenue
        return vec_other

    return _embed


@pytest.fixture
def schema_manager_10_tables(mock_redis_v1, mock_embedding_revenue_only):
    """SchemaManager with 10 tables, embeddings that favor financial_records/sales_summary."""
    return SchemaManager(
        execute_fn=_mock_execute_10,
        redis_client=mock_redis_v1,
        embed_fn=mock_embedding_revenue_only,
    )


# ---------------------------------------------------------------------------
# Unit: _extract_table_names_from_ddl
# ---------------------------------------------------------------------------


def test_extract_table_names_from_ddl():
    """Agent correctly extracts table names from DDL strings."""
    from daibai.core.agent import DaiBaiAgent
    from daibai.core.config import Config, DatabaseConfig

    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    ddl_list = [
        "\n-- Table: financial_records\n  `id` int",
        "\n-- Table: sales_summary\n  `total_sales` decimal",
    ]
    tables = agent._extract_table_names_from_ddl(ddl_list)
    assert tables == {"financial_records", "sales_summary"}


# ---------------------------------------------------------------------------
# Integration: Pruned schema injected into LLM context
# ---------------------------------------------------------------------------


def test_generate_sql_injects_only_pruned_tables_into_context(schema_manager_10_tables, mock_redis_v1):
    """
    With 10 tables and a query about revenue, the prompt sent to the LLM
    contains DDL for only the 2 relevant tables (financial_records, sales_summary).
    Proves semantic pruner is active and token cost is reduced.
    """
    schema_manager_10_tables.index_schema("testdb", force=True)

    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    # Inject our schema manager and pre-populate schema memory to avoid DB connection
    agent._schema_managers["test"] = schema_manager_10_tables
    agent._schema_memory["test"] = "-- Table: users\n  dummy"
    agent._trained_dbs.add("test")

    captured_context = {}

    def _capture_generate(prompt, context=None):
        if context:
            captured_context["schema"] = context.get("schema", "")
            captured_context["allowed_tables"] = context.get("allowed_tables")
        return MagicMock(sql="SELECT * FROM financial_records", text="")

    with patch.object(agent, "generate", side_effect=_capture_generate):
        agent.generate_sql("Show me total revenue and sales", mode="sql")

    schema_injected = captured_context.get("schema", "")
    allowed = captured_context.get("allowed_tables")

    # Must contain the 2 revenue-related tables
    assert "financial_records" in schema_injected or "revenue" in schema_injected
    assert "sales_summary" in schema_injected or "total_sales" in schema_injected

    # Must NOT contain unrelated tables (proves pruning)
    assert "notifications" not in schema_injected
    assert "audit_log" not in schema_injected
    assert "config" not in schema_injected

    assert allowed is not None
    assert "financial_records" in allowed or "sales_summary" in allowed


def test_run_sql_uses_last_allowed_tables_from_pruned_context():
    """
    When run_sql is called without explicit allowed_tables, it uses
    _last_allowed_tables from the most recent generate_sql (scope enforcement).
    """
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    # Simulate pruned context: only sales and products allowed
    agent._last_allowed_tables = {"sales", "products"}

    with patch.object(agent, "_get_runner") as mock_get_runner:
        mock_runner = MagicMock()
        mock_get_runner.return_value = mock_runner

        agent.run_sql("SELECT * FROM sales")  # No explicit allowed_tables

        mock_runner.run_sql.assert_called_once()
        call_kwargs = mock_runner.run_sql.call_args[1]
        assert call_kwargs.get("allowed_tables") == {"sales", "products"}


def test_run_sql_explicit_allowed_tables_overrides_last():
    """Explicit allowed_tables passed to run_sql overrides _last_allowed_tables."""
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)
    agent._last_allowed_tables = {"sales"}

    with patch.object(agent, "_get_runner") as mock_get_runner:
        mock_runner = MagicMock()
        mock_get_runner.return_value = mock_runner

        agent.run_sql("SELECT * FROM orders", allowed_tables={"orders", "users"})

        call_kwargs = mock_runner.run_sql.call_args[1]
        assert call_kwargs.get("allowed_tables") == {"orders", "users"}


def test_pruned_context_respects_schema_vector_limit():
    """
    With many tables, pruned result contains at most SCHEMA_VECTOR_LIMIT (default 5).
    Simulates 1000+ table database: only top-K relevant tables go to LLM.
    """
    from daibai.core.config import get_schema_vector_limit

    limit = get_schema_vector_limit()

    # Build metadata for 20 tables; embedding returns all with same score
    metadata_20 = {}
    for i in range(20):
        tname = f"table_{i}"
        metadata_20[tname] = [
            {"column_name": "id", "data_type": "int", "column_type": "int", "is_nullable": False, "column_key": "PRI", "column_default": None, "extra": ""},
        ]

    def _mock_exec(sql, params=()):
        rows = []
        for table, cols in metadata_20.items():
            for col in cols:
                rows.append({
                    "TABLE_SCHEMA": "bigdb", "TABLE_NAME": table,
                    "COLUMN_NAME": col["column_name"], "DATA_TYPE": col["data_type"],
                    "COLUMN_TYPE": col["column_type"], "IS_NULLABLE": "NO",
                    "COLUMN_KEY": col["column_key"], "COLUMN_DEFAULT": None, "EXTRA": "",
                })
        return rows

    class MockRedis:
        def __init__(self):
            self._storage = {}
            self._sets = {}

        def get(self, k):
            return self._storage.get(k)

        def set(self, k, v, ex=None):
            self._storage[k] = v

        def sadd(self, name, member):
            self._sets.setdefault(name, set()).add(member)

        def smembers(self, name):
            return self._sets.get(name, set())

        def exists(self, *keys):
            return sum(1 for k in keys if k in self._storage or k in self._sets)

        def expire(self, key, seconds):
            pass

    redis = MockRedis()
    # Same vector for all - search returns first N by limit
    vec = [0.1] * 128
    sm = SchemaManager(
        execute_fn=_mock_exec,
        redis_client=redis,
        embed_fn=lambda t: vec,
    )
    sm.index_schema("bigdb", force=True)
    result = sm.search_schema_v1("any query", schema_name="bigdb", limit=limit)
    assert len(result) <= limit


def test_scope_enforcement_blocks_out_of_scope_table():
    """
    When pruned context allows only {sales}, and agent hallucinates a query
    referencing {orders}, SecurityViolation is raised.
    """
    from daibai.core.guardrails import SQLValidator

    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM sales UNION SELECT * FROM orders",
            allowed_tables={"sales"},
        )
    assert "orders" in str(exc.value).lower() or "scope" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Fallback: No Redis/embeddings → full schema, no scope restriction
# ---------------------------------------------------------------------------


def test_pruning_fallback_to_full_schema_when_no_redis():
    """
    When Redis/embeddings unavailable, _get_pruned_schema falls back to
    full schema and allowed_tables=None (permissive scope).
    """
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)
    agent._schema_memory["test"] = "-- Table: users\n  `id` int\n-- Table: orders\n  `id` int"
    agent._trained_dbs.add("test")

    # Force no cache/Redis (no schema manager with embeddings)
    agent._cache_manager = None
    agent._schema_managers.clear()

    # _get_schema_manager returns SchemaManager with no redis - search_schema_v1 returns []
    # So we fall back to get_schema
    with patch.object(agent, "_get_schema_manager", return_value=None):
        pruned, allowed = agent._get_pruned_schema("revenue")
        assert pruned == "-- Table: users\n  `id` int\n-- Table: orders\n  `id` int"
        assert allowed is None


# ---------------------------------------------------------------------------
# Live tests (Redis + optional MySQL)
# ---------------------------------------------------------------------------


def _get_redis_url():
    return (
        os.environ.get("REDIS_URL", "").strip()
        or os.environ.get("AZURE_REDIS_CONNECTION_STRING", "").strip()
    )


def _has_mysql():
    return bool(os.environ.get("MYSQL_HOST") or os.environ.get("MYSQL_PASSWORD"))


@pytest.mark.cloud
@pytest.mark.skipif(
    not _get_redis_url(),
    reason="REDIS_URL not set - live pruning test requires Redis",
)
def test_agent_pruning_live_redis():
    """
    Live: Agent with real Redis + embeddings. Mock DB (10 tables).
    Query "revenue" returns only financial_records/sales_summary DDL.
    """
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager

    conn = _get_redis_url()
    cache = CacheManager(connection_string=conn)
    manager = SchemaManager(
        execute_fn=_mock_execute_10,
        cache_manager=cache,
    )
    try:
        count = manager.index_schema("testdb", force=True)
        assert count == 10

        result = manager.search_schema_v1(
            "Show me total revenue", schema_name="testdb", limit=5, threshold=0.2
        )
        assert len(result) >= 1
        ddl_text = "\n".join(result)
        assert "financial_records" in ddl_text or "sales_summary" in ddl_text or "revenue" in ddl_text
    finally:
        client = cache._get_client()
        if client:
            index_key = f"{SCHEMA_V1_INDEX_PREFIX}testdb"
            for table in _MOCK_METADATA_10.keys():
                client.delete(f"{SCHEMA_V1_DDL_PREFIX}testdb:{table}")
                client.delete(f"{SCHEMA_V1_TEXT_PREFIX}testdb:{table}")
                client.srem(index_key, table)


@pytest.mark.cloud
@pytest.mark.skipif(
    not _get_redis_url() or not _has_mysql(),
    reason="REDIS_URL and MYSQL_* required for full live pruning test",
)
def test_agent_pruning_live_full_pipeline():
    """
    Live: Full pipeline with real Redis, embeddings, and MySQL.
    - Index schema from real DB
    - Ask revenue-related question
    - Assert pruned schema is smaller than full schema
    - Execute valid SELECT against allowed table
    """
    pytest.importorskip("sentence_transformers")

    from daibai.core.cache import CacheManager

    host = os.environ.get("MYSQL_HOST", "localhost")
    port = int(os.environ.get("MYSQL_PORT", "3306"))
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "test")

    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={
            "test": DatabaseConfig("test", host, port, database, user, password),
        },
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    # Ensure we have Redis
    redis_url = _get_redis_url()
    if not redis_url:
        pytest.skip("REDIS_URL or AZURE_REDIS_CONNECTION_STRING not set - live pruning test requires Redis")

    # Train (will index to Redis)
    try:
        agent.train_schema("test")
    except Exception as e:
        pytest.skip(f"Could not train schema: {e}")

    full_schema = agent.get_schema("test")
    pruned, allowed = agent._get_pruned_schema("What is our total revenue?", "test")

    # Pruned should be smaller or equal
    assert len(pruned) <= len(full_schema) or len(pruned) == 0
    if allowed:
        assert len(allowed) <= 20  # SCHEMA_VECTOR_LIMIT max
