"""
Schema discovery for database metadata.

Uses information_schema to extract table names, column names, and data types
for grounding SQL generation. Supports semantic schema mapping: vectorize
table DDLs and retrieve only relevant tables for a given query (table pruning).

Phase 3 Step 1: High-precision semantic schema indexing with discover_schema(),
index_schema(), and search_schema_v1() using schema:v1:* Redis keys.
"""

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import DatabaseConfig, get_schema_refresh_interval, get_schema_vector_limit
from .instrumentation import track_start, track_underway, track_passed, track_failed, init_tracker

logger = logging.getLogger(__name__)


def _sanitize_redis_url(url: Optional[str]) -> str:
    """Mask password in Redis URL for safe logging."""
    if not url:
        return "(not configured)"
    # rediss://:password@host:port or redis://:password@host
    m = re.match(r"(rediss?://)(:[^@]*@)(.+)", url)
    if m:
        return f"{m.group(1)}:***@{m.group(3).split('?')[0]}"
    # Fallback: hide from first @ to next @ or end
    if "@" in url:
        return re.sub(r":([^@]+)@", ":***@", url, count=1)
    return url


def _sanitize_db_password(pwd: Optional[str]) -> str:
    """Mask password for safe logging."""
    if not pwd:
        return "(empty)"
    return "***"


def _log_index_connection(
    db: str,
    config: Optional[DatabaseConfig] = None,
    is_playground: bool = False,
) -> None:
    """Log full database connection details at index start (passwords masked)."""
    try:
        from .config import get_redis_connection_string
        redis_target = _sanitize_redis_url(get_redis_connection_string())
    except Exception:
        redis_target = "(unknown)"
    if is_playground:
        logger.info(
            "[index] connection: db=%s | type=SQLite | source=data/playground.db | "
            "library=sqlite3 | redis_target=%s",
            db, redis_target,
        )
        return
    if not config:
        logger.info(
            "[index] connection: db=%s | type=unknown | no config | redis_target=%s",
            db, redis_target,
        )
        return
    endpoint = f"{config.host}:{config.port}"
    url_safe = f"mysql://{config.user}:{_sanitize_db_password(config.password)}@{endpoint}/{config.database}"
    if config.ssl:
        url_safe += "?ssl=true"
    logger.info(
        "[index] connection: db=%s | type=MySQL | host=%s | port=%s | database=%s | "
        "user=%s | password=%s | ssl=%s | url=%s | redis_target=%s",
        db,
        config.host,
        config.port,
        config.database,
        config.user,
        _sanitize_db_password(config.password),
        config.ssl,
        url_safe,
        redis_target,
    )


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
# All keys are namespaced by db_name to avoid collision across databases.
SCHEMA_V1_PREFIX = "schema:v1:"
SCHEMA_V1_DDL_PREFIX = "schema:v1:ddl:"  # full key: schema:v1:ddl:{db}:{table}
SCHEMA_V1_TEXT_PREFIX = "schema:v1:text:"  # full key: schema:v1:text:{db}:{table}
SCHEMA_V1_INDEX_PREFIX = "schema:v1:index:"  # full key: schema:v1:index:{db} (Redis SET)
SCHEMA_V1_LAST_INDEXED = "schema:v1:last_indexed"

# Schema indexing status keys (suffixed with :{db} at runtime)
SCHEMA_STATUS_IS_INDEXED      = "schema:status:is_indexed"
SCHEMA_STATUS_LAST_INDEXED_AT = "schema:status:last_indexed_at"
SCHEMA_STATUS_DDL_HASH        = "schema:status:ddl_hash"


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


def index_all_startup(
    get_schema_manager: "Callable[[str], Optional[SchemaManager]]",
    progress_cb: Optional[Callable[[float, str, float], None]] = None,
) -> int:
    """
    Index all managed databases at startup (daibai.yaml DBs + playground).

    For each database, calls get_schema_manager(db_name) to obtain a SchemaManager,
    then invokes index_schema which implements evergreening (TTL refresh if
    already indexed) or full indexing.

    Args:
        get_schema_manager: Callable(db_name) -> SchemaManager | None. Returns
            None if the db cannot be indexed (e.g. not configured).
        progress_cb: Optional callable(pct, status, eta_seconds) for overall
            progress across all databases (0-100%).

    Returns:
        Total number of tables indexed across all databases.
    """
    from .config import load_config

    config = load_config()
    databases = list(config.databases.keys()) + ["playground"]
    total_dbs = len(databases)
    total_tables = 0
    logger.info(
        "[index] startup: indexing %d database(s): %s",
        total_dbs, ", ".join(databases),
    )

    for i, db_name in enumerate(databases):
        sm = get_schema_manager(db_name)
        if not sm:
            logger.warning("[index] %s: no schema manager available, skipping", db_name)
            continue

        def _per_db_progress(pct: float, status: str, eta: float) -> None:
            base = (i / total_dbs) * 100.0
            width = 100.0 / total_dbs
            overall_pct = base + (pct / 100.0) * width
            if progress_cb is not None:
                try:
                    progress_cb(overall_pct, f"[{db_name}] {status}", eta)
                except Exception:
                    pass

        total_tables += sm.index_schema(db_name, progress_cb=_per_db_progress)

    return total_tables


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

        For each table: generates embedding, stores vector in schema:v1:ddl:{db}:{table},
        raw DDL in schema:v1:text:{db}:{table}, and adds table to schema:v1:index:{db}.

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

        _log_index_connection(
            db,
            config=self._config,
            is_playground=self._execute_fn is not None,
        )

        redis = self._get_redis()
        if redis is None:
            logger.warning("[index] %s: skipped — no Redis client", db)
            return 0

        init_tracker(f"Startup Index: {db}")
        track_start(f"Schema Indexing: {db}")

        # ── Evergreening: if already indexed, extend TTL and return ───────────
        is_indexed_key = f"{SCHEMA_STATUS_IS_INDEXED}:{db}"
        try:
            if redis.exists(is_indexed_key):
                tables_ddl = self.discover_schema(schema_name)
                if not tables_ddl:
                    logger.info("[index] %s: no tables found (evergreen skip)", db)
                    track_passed(f"Schema Indexing: {db}", "No tables to evergreen.")
                    return 0
                evergreen_count = 0
                index_key = f"{SCHEMA_V1_INDEX_PREFIX}{db}"
                for table in tables_ddl:
                    ddl_key = f"{SCHEMA_V1_DDL_PREFIX}{db}:{table}"
                    text_key = f"{SCHEMA_V1_TEXT_PREFIX}{db}:{table}"
                    if redis.exists(ddl_key):
                        redis.expire(ddl_key, 86400)
                        evergreen_count += 1
                    if redis.exists(text_key):
                        redis.expire(text_key, 86400)
                if redis.exists(index_key):
                    redis.expire(index_key, 86400)
                for status_key in [
                    f"{SCHEMA_STATUS_IS_INDEXED}:{db}",
                    f"{SCHEMA_STATUS_LAST_INDEXED_AT}:{db}",
                    f"{SCHEMA_STATUS_DDL_HASH}:{db}",
                    f"{SCHEMA_V1_LAST_INDEXED}:{db}",
                ]:
                    if redis.exists(status_key):
                        redis.expire(status_key, 86400)
                track_passed(
                    f"Schema Indexing: {db}",
                    f"Database already in Redis. Evergreened TTL back to 24 hours for schema:v1:ddl/text keys and schema:status:*:{db}",
                )
                return evergreen_count
        except Exception as e:
            track_failed(f"Schema Indexing: {db}", f"Evergreen check failed: {str(e)}")
            raise

        track_underway(
            f"Schema Indexing: {db}",
            f"About to index {db} because it is a managed database. Indexing using SentenceTransformer (local) to target Redis instance.",
        )

        db_library = "sqlite3 (custom execute_fn)" if self._execute_fn else "mysql-connector-python"
        embed_source = (
            "sentence_transformers (all-MiniLM-L6-v2)"
            if self._cache_manager
            else ("custom embed_fn" if self._embed_fn else "none")
        )
        config_loc = (
            "daibai.yaml databases"
            if self._config
            else "project data/playground.db"
        )
        try:
            from .config import get_redis_connection_string
            redis_loc = _sanitize_redis_url(get_redis_connection_string())
        except Exception:
            redis_loc = "(unknown)"
        logger.info(
            "[index] %s: indexing using %s against %s from %s | redis=%s",
            db, db_library, embed_source, config_loc, redis_loc,
        )
        logger.info("[index] %s: start (force=%s)", db, force)

        # Refresh-interval check removed: always re-index on focus (app load, dropdown, playground).
        # if not force:
        #     last_key = f"{SCHEMA_V1_LAST_INDEXED}:{db}"
        #     try:
        #         last_raw = redis.get(last_key)
        #         if last_raw:
        #             last_ts = float(last_raw)
        #             interval = get_schema_refresh_interval()
        #             if time.time() - last_ts < interval:
        #                 logger.info("[index] %s: skipped — SCHEMA_REFRESH_INTERVAL not elapsed", db)
        #                 return 0
        #     except (ValueError, TypeError):
        #         pass

        tables_ddl = self.discover_schema(schema_name)
        if not tables_ddl:
            logger.info("[index] %s: no tables found", db)
            return 0

        total = len(tables_ddl)
        logger.info("[index] %s: discovered %d table(s)", db, total)

        # Compute DDL hash for change detection (sorted table names + DDL for consistency)
        ddl_str = "\n".join(f"{t}:{ddl}" for t, ddl in sorted(tables_ddl.items()))
        ddl_hash = hashlib.sha256(ddl_str.encode()).hexdigest()

        stored = 0
        bytes_written = 0
        # Rolling window of the last 5 per-table durations for ETA estimation.
        _recent_times: List[float] = []

        try:
            for i, (table, ddl) in enumerate(tables_ddl.items()):
                t0 = time.time()

                vector = self._get_embedding(ddl)
                if vector is None:
                    track_failed(
                        f"Schema Indexing: {db}",
                        "Index failed: embedding model unavailable (sentence_transformers or embed_fn returned None)",
                    )
                    logger.critical(
                        "[index] %s: failed — embedding model unavailable (sentence_transformers all-MiniLM-L6-v2 "
                        "or embed_fn returned None); falling back to Lazy Discovery",
                        db,
                    )
                    return 0

                ddl_key  = f"{SCHEMA_V1_DDL_PREFIX}{db}:{table}"
                text_key = f"{SCHEMA_V1_TEXT_PREFIX}{db}:{table}"
                index_key = f"{SCHEMA_V1_INDEX_PREFIX}{db}"
                try:
                    vector_json = json.dumps(vector)
                    redis.set(ddl_key, vector_json, ex=ttl)
                    redis.set(text_key, ddl, ex=ttl)
                    redis.sadd(index_key, table)
                    stored += 1
                    bytes_written += len(vector_json.encode("utf-8")) + len(ddl.encode("utf-8"))
                except Exception as e:
                    logger.warning("[index] %s: failed to store table %s — %s", db, table, e)

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
                    redis.set(f"{SCHEMA_STATUS_DDL_HASH}:{db}",      ddl_hash,         ex=ttl)
                    try:
                        from .config import get_redis_connection_string
                        redis_loc = _sanitize_redis_url(get_redis_connection_string())
                    except Exception:
                        redis_loc = "(unknown)"
                    size_str = f"{bytes_written / 1024:.1f}KB" if bytes_written < 1024 * 1024 else f"{bytes_written / (1024 * 1024):.2f}MB"
                    key_names = f"{SCHEMA_V1_DDL_PREFIX}{db}:*, {SCHEMA_V1_TEXT_PREFIX}{db}:*, {SCHEMA_V1_INDEX_PREFIX}{db}, schema:status:*"
                    logger.info(
                        "[index] %s: complete — loaded to redis=%s keyed by %s | "
                        "tables=%d size=%s last_indexed_at=%s",
                        db, redis_loc, key_names, stored, size_str, now_iso,
                    )
                    approx_kb = len(str(tables_ddl)) / 1024
                    track_passed(
                        f"Schema Indexing: {db}",
                        f"Index completed and loaded to Redis keyed by schema:v1:*:{db}. Size approx {approx_kb:.2f} KB",
                    )
                except Exception as e:
                    logger.warning("[index] %s: failed to write status keys — %s", db, e)
            else:
                logger.info("[index] %s: complete — 0 tables stored", db)
                track_passed(f"Schema Indexing: {db}", "Index completed (0 tables stored).")

        except Exception as e:
            track_failed(f"Schema Indexing: {db}", f"Index failed: {str(e)}")
            logger.critical(
                "[index] %s: failed — %s: %s; falling back to Lazy Discovery",
                db, type(e).__name__, str(e),
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

        Vectorizes the query, compares against stored schema:v1:ddl:{db}:* vectors,
        and returns the top N matching DDL strings from schema:v1:text:{db}:*.

        Args:
            query: Natural language question (e.g. "How much money did we make?").
            schema_name: Database/schema to search. Required for namespaced lookup.
            limit: Max tables to return (default from SCHEMA_VECTOR_LIMIT).
            threshold: Minimum cosine similarity.

        Returns:
            List of DDL strings for the most relevant tables.
        """
        db = schema_name or (self._config.database if self._config else None)
        if not db:
            return []

        query_vector = self._get_embedding(query)
        if query_vector is None:
            return []

        redis = self._get_redis()
        if redis is None:
            return []

        limit = limit or get_schema_vector_limit()
        index_key = f"{SCHEMA_V1_INDEX_PREFIX}{db}"
        try:
            raw_names = list(redis.smembers(index_key)) or []
            table_names = [
                t.decode("utf-8") if isinstance(t, bytes) else t for t in raw_names
            ]
        except Exception:
            table_names = []

        scored: List[Tuple[float, str]] = []
        for table in table_names:
            ddl_key = f"{SCHEMA_V1_DDL_PREFIX}{db}:{table}"
            text_key = f"{SCHEMA_V1_TEXT_PREFIX}{db}:{table}"
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
