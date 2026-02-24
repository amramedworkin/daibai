"""
Schema discovery for database metadata.

Uses information_schema to extract table names, column names, and data types
for grounding SQL generation. Produces structured metadata or DDL-like text.
"""

from typing import Any, Callable, Dict, List, Optional, Union

from .config import DatabaseConfig


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


class SchemaManager:
    """
    Extracts and structures database schema metadata.

    Uses information_schema (MySQL) to build a DDL-like representation
    for grounding LLM SQL generation.
    """

    def __init__(
        self,
        config: Optional[DatabaseConfig] = None,
        execute_fn: Optional[Callable[[str, tuple], List[Dict[str, Any]]]] = None,
    ):
        """
        Initialize SchemaManager.

        Args:
            config: DatabaseConfig for connection (used when execute_fn is None).
            execute_fn: Optional callable(sql, params) -> list of dict rows.
                        Used for testing with mocked connections.
        """
        self._config = config
        self._execute_fn = execute_fn

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
