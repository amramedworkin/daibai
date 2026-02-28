"""
Schema discovery for database metadata.

Uses information_schema to extract table names, column names, and data types
for grounding SQL generation. Supports semantic schema mapping: vectorize
table DDLs and retrieve only relevant tables for a given query (table pruning).

Phase 3 Step 1: High-precision semantic schema indexing with discover_schema(),
index_schema(), and search_schema_v1() using schema:v1:* Redis keys.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import DatabaseConfig, get_schema_refresh_interval, get_schema_vector_limit

logger = logging.getLogger(__name__)


# Standard MySQL information_schema query for columns
_SCHEMA_QUERY = """
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE,
    COLUMN_TYPE,
    IS_NULLABLE,
    COLUMN_KEY,
    COLUMN_DEFAULT,
    EXTRA
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""


SCHEMA_VECTOR_PREFIX = "schema:"
SCHEMA_INDEX_KEY = "schema:index"

# Phase 3 Step 1: High-precision semantic schema index (v1)
SCHEMA_V1_PREFIX = "schema:v1:"
SCHEMA_V1_DDL_PREFIX = "schema:v1:ddl:"
SCHEMA_V1_TEXT_PREFIX = "schema:v1:text:"
SCHEMA_V1_INDEX_KEY = "schema:v1:index"
SCHEMA_V1_LAST_INDEXED = "schema:v1:last_indexed"

# Schema indexing status keys (suffixed with :{db} at runtime)
SCHEMA_STATUS_IS_INDEXED    = "schema:status:is_indexed"
SCHEMA_STATUS_LAST_INDEXED_AT = "schema:status:last_indexed_at"


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors (0.0 to 1.0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SchemaManager:
    """
    Extracts and structures database schema metadata.

    Uses information_schema (MySQL) to build a DDL-like representation
    for grounding LLM SQL generation. Supports semantic table pruning
    via vectorize_schema() and get_relevant_tables().
    """

    def __init__(
        self,
        config: Optional[DatabaseConfig] = None,
        execute_fn: Optional[Callable[[str, tuple], List[Dict[str, Any]]]] = None,
        cache_manager=None,
        embed_fn: Optional[Callable[[str], Optional[List[float]]]] = None,
        redis_client=None,
    ):
        """
        Initialize SchemaManager.

        Args:
            config: DatabaseConfig for connection (used when execute_fn is None).
            execute_fn: Optional callable(sql, params) -> list of dict rows.
                        Used for testing with mocked connections.
            cache_manager: Optional CacheManager for Redis + embeddings.
            embed_fn: Optional callable(text) -> vector. Used for testing.
            redis_client: Optional Redis client (dict-like get/set/keys).
                         Used for testing when cache_manager is None.
        """
        self._config = config
        self._execute_fn = execute_fn
        self._cache_manager = cache_manager
        self._embed_fn = embed_fn
        self._redis_client = redis_client

    def _run_query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute SQL and return rows as list of dicts."""
        if self._execute_fn is not None:
            return self._execute_fn(sql, params)
        if self._config is None:
            raise ValueError("SchemaManager requires config or execute_fn")
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=self._config.host,
                port=self._config.port,
                database=self._config.database,
                user=self._config.user,
                password=self._config.password,
            )
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(sql, params)
                return cursor.fetchall()
            finally:
                conn.close()
        except ImportError:
            raise ImportError("MySQL support requires mysql-connector-python")

    def get_schema_metadata(
        self,
        schema_name: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Query the database for table and column metadata.

        Returns a structured dict: {table_name: [column_info, ...]}.
        Each column_info has: column_name, data_type, column_type, is_nullable,
        column_key, column_default, extra.

        Args:
            schema_name: Database/schema to query. Defaults to config.database.

        Returns:
            Dict mapping table names to lists of column metadata dicts.
        """
        db = schema_name or (self._config.database if self._config else None)
        if not db:
            raise ValueError("schema_name or config.database required")

        rows = self._run_query(_SCHEMA_QUERY, (db,))
        tables: Dict[str, List[Dict[str, Any]]] = {}

        for row in rows:
            table = row.get("TABLE_NAME", "")
            if table not in tables:
                tables[table] = []
            tables[table].append({
                "column_name": row.get("COLUMN_NAME"),
                "data_type": row.get("DATA_TYPE"),
                "column_type": row.get("COLUMN_TYPE"),
                "is_nullable": row.get("IS_NULLABLE", "YES") == "YES",
                "column_key": row.get("COLUMN_KEY") or "",
                "column_default": row.get("COLUMN_DEFAULT"),
                "extra": row.get("EXTRA") or "",
            })

        return tables

    def get_schema_ddl(
        self,
        schema_name: Optional[str] = None,
        max_tables: int = 100,
    ) -> str:
        """
        Build a DDL-like string from schema metadata for LLM context.

        Args:
            schema_name: Database/schema to query.
            max_tables: Maximum tables to include (default 100).

        Returns:
            Multi-line string suitable for prompt context.
        """
        metadata = self.get_schema_metadata(schema_name)
        lines = []
        for table, columns in list(metadata.items())[:max_tables]:
            lines.append(f"\n-- Table: {table}")
            for col in columns:
                nullable = "NULL" if col["is_nullable"] else "NOT NULL"
                key = f" {col['column_key']}" if col["column_key"] else ""
                default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
                extra = f" {col['extra']}" if col["extra"] else ""
                col_type = col["column_type"] or col["data_type"] or "unknown"
                lines.append(f"  `{col['column_name']}` {col_type}{key}{nullable}{default}{extra}")
        return "\n".join(lines) if lines else ""

    def _table_ddl(self, metadata: Dict[str, List[Dict[str, Any]]], table: str) -> str:
        """Build DDL string for a single table."""
        columns = metadata.get(table, [])
        lines = [f"\n-- Table: {table}"]
        for col in columns:
            nullable = "NULL" if col["is_nullable"] else "NOT NULL"
            key = f" {col['column_key']}" if col["column_key"] else ""
            default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
            extra = f" {col['extra']}" if col["extra"] else ""
            col_type = col["column_type"] or col["data_type"] or "unknown"
            lines.append(f"  `{col['column_name']}` {col_type}{key}{nullable}{default}{extra}")
        return "\n".join(lines)

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text. Uses embed_fn, cache_manager, or None."""
        if self._embed_fn is not None:
            return self._embed_fn(text)
        if self._cache_manager is not None:
            return self._cache_manager.get_embedding(text)
        return None

    def _get_redis(self):
        """Get Redis client for schema storage."""
        if self._redis_client is not None:
            return self._redis_client
        if self._cache_manager is not None:
            return self._cache_manager._get_client()
        return None

    def vectorize_schema(
        self,
        schema_name: Optional[str] = None,
        ttl: int = 86400,
    ) -> int:
        """
        Vectorize table DDLs and store in Redis (idx:schema_vectors).

        Returns the number of tables stored.
        """
        metadata = self.get_schema_metadata(schema_name)
        db = schema_name or (self._config.database if self._config else None)
        if not db:
            raise ValueError("schema_name or config.database required")

        redis = self._get_redis()
        if redis is None:
            return 0

        index_key = f"{SCHEMA_VECTOR_PREFIX}{db}:index"
        stored = 0
        for table, columns in metadata.items():
            ddl = self._table_ddl(metadata, table)
            vector = self._get_embedding(ddl)
            if vector is None:
                continue
            key = f"{SCHEMA_VECTOR_PREFIX}{db}:{table}"
            payload = {"ddl": ddl, "table": table, "vector": vector}
            try:
                redis.set(key, json.dumps(payload), ex=ttl)
                redis.sadd(index_key, table)
                stored += 1
            except Exception:
                pass
        return stored

    def get_relevant_tables(
        self,
        query: str,
        schema_name: Optional[str] = None,
        limit: Optional[int] = None,
        threshold: float = 0.3,
    ) -> List[str]:
        """
        Semantic search: return DDL strings for tables most relevant to the query.

        Vectorizes the query, compares against stored schema vectors, returns
        top N table DDLs (default from SCHEMA_VECTOR_LIMIT, 5).

        Args:
            query: Natural language question (e.g. "What are employee salaries?").
            schema_name: Database/schema to search.
            limit: Max tables to return (default from SCHEMA_VECTOR_LIMIT).
            threshold: Minimum cosine similarity (default 0.3).

        Returns:
            List of DDL strings for the most relevant tables.
        """
        db = schema_name or (self._config.database if self._config else None)
        if not db:
            raise ValueError("schema_name or config.database required")

        limit = limit or get_schema_vector_limit()
        query_vector = self._get_embedding(query)
        if query_vector is None:
            return []

        redis = self._get_redis()
        if redis is None:
            return []

        index_key = f"{SCHEMA_VECTOR_PREFIX}{db}:index"
        try:
            raw_names = list(redis.smembers(index_key)) or []
            table_names = [
                t.decode("utf-8") if isinstance(t, bytes) else t for t in raw_names
            ]
        except Exception:
            table_names = []

        scored: List[Tuple[float, str]] = []
        for table in table_names:
            key = f"{SCHEMA_VECTOR_PREFIX}{db}:{table}"
            raw = redis.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            vector = payload.get("vector")
            ddl = payload.get("ddl", "")
            if not vector or not isinstance(vector, list):
                continue
            sim = _cosine_similarity(query_vector, vector)
            if sim >= threshold:
                scored.append((sim, ddl))

        scored.sort(key=lambda x: -x[0])
        return [ddl for _, ddl in scored[:limit]]

    # -------------------------------------------------------------------------
    # Phase 3 Step 1: High-precision semantic schema index (v1)
    # -------------------------------------------------------------------------

    def discover_schema(self, schema_name: Optional[str] = None) -> Dict[str, str]:
        """
        Discover all tables and return DDL strings.

        Queries INFORMATION_SCHEMA.COLUMNS and aggregates into a dict where
        the key is the table name and the value is a DDL-like string
        (e.g. "CREATE TABLE users (id INT, email TEXT)").

        Args:
            schema_name: Database/schema to query. Defaults to config.database.

        Returns:
            Dict mapping table names to DDL strings.
        """
        metadata = self.get_schema_metadata(schema_name)
        result: Dict[str, str] = {}
        for table, columns in metadata.items():
            ddl = self._table_ddl(metadata, table)
            # Normalize to CREATE TABLE style for consistency
            result[table] = ddl.strip()
        return result

    def index_schema(
        self,
        schema_name: Optional[str] = None,
        force: bool = False,
        ttl: int = 86400,
        progress_cb: Optional[Callable[[float, str, float], None]] = None,
    ) -> int:
        """
        Index schema into Redis using v1 key format for semantic search.

        For each table: generates embedding, stores vector in schema:v1:ddl:{table},
        raw DDL in schema:v1:text:{table}, and adds table to schema:v1:index.

        Guardrails:
        - If EmbeddingEngine is unavailable, logs CRITICAL and returns 0 (Lazy Discovery mode).
        - Skips re-indexing if SCHEMA_REFRESH_INTERVAL has not passed (unless force=True).

        Args:
            schema_name: Database/schema to index.
            force: If True, bypass refresh interval check.
            ttl: Redis TTL in seconds for stored keys.
            progress_cb: Optional callable(pct, status, eta_seconds) fired after each
                table.  ``pct`` is 0-100, ``eta_seconds`` is a simple moving average
                estimate of seconds remaining.  Exceptions inside the callback are
                silently swallowed so they never abort indexing.

        Returns:
            Number of tables indexed.
        """
        db = schema_name or (self._config.database if self._config else None)
        if not db:
            raise ValueError("schema_name or config.database required")

        redis = self._get_redis()
        if redis is None:
            logger.warning("Schema indexing skipped: no Redis client")
            return 0

        # Check refresh interval (skipped when force=True)
        if not force:
            last_key = f"{SCHEMA_V1_LAST_INDEXED}:{db}"
            try:
                last_raw = redis.get(last_key)
                if last_raw:
                    last_ts = float(last_raw)
                    interval = get_schema_refresh_interval()
                    if time.time() - last_ts < interval:
                        logger.debug(
                            "Schema indexing skipped: SCHEMA_REFRESH_INTERVAL not elapsed"
                        )
                        return 0
            except (ValueError, TypeError):
                pass

        tables_ddl = self.discover_schema(schema_name)
        if not tables_ddl:
            return 0

        total = len(tables_ddl)
        stored = 0
        # Rolling window of the last 5 per-table durations for ETA estimation.
        _recent_times: List[float] = []

        try:
            for i, (table, ddl) in enumerate(tables_ddl.items()):
                t0 = time.time()

                vector = self._get_embedding(ddl)
                if vector is None:
                    logger.critical(
                        "Embedding model unavailable; schema indexing falling back to Lazy Discovery"
                    )
                    return 0

                ddl_key  = f"{SCHEMA_V1_DDL_PREFIX}{table}"
                text_key = f"{SCHEMA_V1_TEXT_PREFIX}{table}"
                try:
                    redis.set(ddl_key, json.dumps(vector), ex=ttl)
                    redis.set(text_key, ddl, ex=ttl)
                    redis.sadd(SCHEMA_V1_INDEX_KEY, table)
                    stored += 1
                except Exception as e:
                    logger.warning("Failed to store schema for table %s: %s", table, e)

                # ── Progress reporting ───────────────────────────────────────
                elapsed = time.time() - t0
                _recent_times.append(elapsed)
                if len(_recent_times) > 5:      # keep a 5-table rolling window
                    _recent_times.pop(0)
                avg_per_table = sum(_recent_times) / len(_recent_times)
                remaining_tables = total - (i + 1)
                eta = avg_per_table * remaining_tables
                pct = ((i + 1) / total) * 100.0

                if progress_cb is not None:
                    try:
                        progress_cb(pct, f"Vectorizing: {table}", eta)
                    except Exception:
                        pass   # progress errors must never abort indexing

            if stored > 0:
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    redis.set(f"{SCHEMA_V1_LAST_INDEXED}:{db}",      str(time.time()), ex=ttl)
                    redis.set(f"{SCHEMA_STATUS_IS_INDEXED}:{db}",     "1",              ex=ttl)
                    redis.set(f"{SCHEMA_STATUS_LAST_INDEXED_AT}:{db}", now_iso,          ex=ttl)
                except Exception:
                    pass

        except Exception as e:
            logger.critical(
                "Schema indexing failed: %s; falling back to Lazy Discovery",
                e,
                exc_info=True,
            )
            return 0

        return stored

    def search_schema_v1(
        self,
        query: str,
        schema_name: Optional[str] = None,
        limit: Optional[int] = None,
        threshold: float = 0.3,
    ) -> List[str]:
        """
        Semantic search over v1-indexed schema.

        Vectorizes the query, compares against stored schema:v1:ddl:* vectors,
        and returns the top N matching DDL strings from schema:v1:text:*.

        Args:
            query: Natural language question (e.g. "How much money did we make?").
            schema_name: Unused for v1 (all tables in index); kept for API consistency.
            limit: Max tables to return (default from SCHEMA_VECTOR_LIMIT).
            threshold: Minimum cosine similarity.

        Returns:
            List of DDL strings for the most relevant tables.
        """
        query_vector = self._get_embedding(query)
        if query_vector is None:
            return []

        redis = self._get_redis()
        if redis is None:
            return []

        limit = limit or get_schema_vector_limit()
        try:
            raw_names = list(redis.smembers(SCHEMA_V1_INDEX_KEY)) or []
            table_names = [
                t.decode("utf-8") if isinstance(t, bytes) else t for t in raw_names
            ]
        except Exception:
            table_names = []

        scored: List[Tuple[float, str]] = []
        for table in table_names:
            ddl_key = f"{SCHEMA_V1_DDL_PREFIX}{table}"
            text_key = f"{SCHEMA_V1_TEXT_PREFIX}{table}"
            raw_vector = redis.get(ddl_key)
            raw_ddl = redis.get(text_key)
            if not raw_vector or not raw_ddl:
                continue
            try:
                vector = json.loads(raw_vector)
            except json.JSONDecodeError:
                continue
            if not isinstance(vector, list):
                continue
            sim = _cosine_similarity(query_vector, vector)
            if sim >= threshold:
                scored.append((sim, raw_ddl))

        scored.sort(key=lambda x: -x[0])
        return [ddl for _, ddl in scored[:limit]]
