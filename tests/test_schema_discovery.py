"""
Tests for SchemaManager and automated schema discovery.

Uses mocked database connections to verify get_schema_metadata()
correctly transforms raw information_schema rows into structured metadata.
"""

import pytest

from daibai.core.config import DatabaseConfig
from daibai.core.schema import SchemaManager, _SCHEMA_QUERY


# Raw rows as returned by information_schema.COLUMNS (MySQL)
_RAW_ROWS = [
    {
        "TABLE_SCHEMA": "testdb",
        "TABLE_NAME": "users",
        "COLUMN_NAME": "id",
        "DATA_TYPE": "int",
        "COLUMN_TYPE": "int",
        "IS_NULLABLE": "NO",
        "COLUMN_KEY": "PRI",
        "COLUMN_DEFAULT": None,
        "EXTRA": "auto_increment",
    },
    {
        "TABLE_SCHEMA": "testdb",
        "TABLE_NAME": "users",
        "COLUMN_NAME": "name",
        "DATA_TYPE": "varchar",
        "COLUMN_TYPE": "varchar(255)",
        "IS_NULLABLE": "YES",
        "COLUMN_KEY": "",
        "COLUMN_DEFAULT": None,
        "EXTRA": "",
    },
    {
        "TABLE_SCHEMA": "testdb",
        "TABLE_NAME": "orders",
        "COLUMN_NAME": "order_id",
        "DATA_TYPE": "int",
        "COLUMN_TYPE": "int",
        "IS_NULLABLE": "NO",
        "COLUMN_KEY": "PRI",
        "COLUMN_DEFAULT": None,
        "EXTRA": "",
    },
    {
        "TABLE_SCHEMA": "testdb",
        "TABLE_NAME": "orders",
        "COLUMN_NAME": "user_id",
        "DATA_TYPE": "int",
        "COLUMN_TYPE": "int",
        "IS_NULLABLE": "YES",
        "COLUMN_KEY": "MUL",
        "COLUMN_DEFAULT": None,
        "EXTRA": "",
    },
]


@pytest.fixture
def mock_execute():
    """Mock execute function that returns raw information_schema rows."""

    def _execute(sql: str, params: tuple = ()):
        assert "information_schema" in sql.lower() or "COLUMNS" in sql
        assert params == ("testdb",)
        return _RAW_ROWS.copy()

    return _execute


@pytest.fixture
def schema_manager(mock_execute):
    """SchemaManager with mocked database."""
    return SchemaManager(execute_fn=mock_execute)


def test_get_schema_metadata_returns_structured_dict(schema_manager):
    """get_schema_metadata transforms raw rows into {table: [columns]}."""
    result = schema_manager.get_schema_metadata("testdb")

    assert "users" in result
    assert "orders" in result
    assert len(result["users"]) == 2
    assert len(result["orders"]) == 2


def test_get_schema_metadata_column_structure(schema_manager):
    """Each column has column_name, data_type, column_type, is_nullable, etc."""
    result = schema_manager.get_schema_metadata("testdb")

    users_cols = result["users"]
    id_col = next(c for c in users_cols if c["column_name"] == "id")
    assert id_col["data_type"] == "int"
    assert id_col["column_type"] == "int"
    assert id_col["is_nullable"] is False
    assert id_col["column_key"] == "PRI"
    assert id_col["extra"] == "auto_increment"

    name_col = next(c for c in users_cols if c["column_name"] == "name")
    assert name_col["data_type"] == "varchar"
    assert name_col["column_type"] == "varchar(255)"
    assert name_col["is_nullable"] is True
    assert name_col["column_key"] == ""


def test_get_schema_ddl_produces_readable_string(schema_manager):
    """get_schema_ddl produces DDL-like text for LLM context."""
    ddl = schema_manager.get_schema_ddl("testdb")

    assert "-- Table: users" in ddl
    assert "-- Table: orders" in ddl
    assert "`id`" in ddl
    assert "`name`" in ddl
    assert "int" in ddl
    assert "varchar(255)" in ddl
    assert "PRI" in ddl or "NOT NULL" in ddl
    assert "auto_increment" in ddl


def test_get_schema_metadata_empty_database(mock_execute):
    """Empty database returns empty dict."""
    def empty_execute(sql, params):
        return []

    manager = SchemaManager(execute_fn=empty_execute)
    result = manager.get_schema_metadata("empty_db")
    assert result == {}


def test_get_schema_ddl_empty_returns_empty_string(mock_execute):
    """get_schema_ddl with no tables returns empty string."""
    def empty_execute(sql, params):
        return []

    manager = SchemaManager(execute_fn=empty_execute)
    ddl = manager.get_schema_ddl("empty_db")
    assert ddl == ""


def test_schema_manager_requires_config_or_execute_fn():
    """SchemaManager raises if neither config nor execute_fn provided."""
    manager = SchemaManager()
    with pytest.raises(ValueError, match="config or execute_fn"):
        manager.get_schema_metadata("testdb")


def test_schema_manager_requires_schema_name_with_execute_fn(mock_execute):
    """get_schema_metadata requires schema_name when using execute_fn (no config)."""
    manager = SchemaManager(execute_fn=mock_execute)
    with pytest.raises(ValueError, match="schema_name or config.database"):
        manager.get_schema_metadata()
