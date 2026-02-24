"""
Schema discovery for database metadata.

Uses information_schema to extract table names, column names, and data types
for grounding SQL generation. Supports semantic schema mapping: vectorize
table DDLs and retrieve only relevant tables for a given query (table pruning).
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import DatabaseConfig, get_schema_vector_limit


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
            table_names = list(redis.smembers(index_key)) or []
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
