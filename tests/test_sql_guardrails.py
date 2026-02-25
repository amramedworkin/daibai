"""
Phase 3 Step 3: SQL Guardrail tests.

Validates SQLValidator (lexical block, scope check, injection shield).
Mock tests use in-memory validator; live tests validate against real MySQL when configured.
"""

import os
import pytest

from daibai.core.guardrails import (
    GuardrailPipeline,
    SQLValidator,
    SecurityViolation,
)


# ---------------------------------------------------------------------------
# Cycle 1 TDD Harness (task-spec: allowed_tables = users, orders, products)
# ---------------------------------------------------------------------------

ALLOWED_TABLES = {"users", "orders", "products"}


def test_tdd_dml_block():
    """RED→GREEN: DELETE FROM users must raise SecurityViolation."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("DELETE FROM users", allowed_tables=ALLOWED_TABLES)
    assert exc.value.layer == "lexical"


def test_tdd_injection_block():
    """RED→GREEN: SELECT * FROM users; DROP TABLE orders; must raise (multi-statement)."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("SELECT * FROM users; DROP TABLE orders;", allowed_tables=ALLOWED_TABLES)
    assert exc.value.layer == "injection"


def test_tdd_out_of_scope_block():
    """RED→GREEN: SELECT * FROM passwords must raise (passwords not in allowed_tables)."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("SELECT * FROM passwords", allowed_tables=ALLOWED_TABLES)
    assert exc.value.layer == "scope"
    assert "passwords" in str(exc.value)


def test_tdd_positive_join_with_aliases():
    """RED→GREEN: SELECT u.name FROM users u JOIN orders o ON u.id = o.user_id WHERE o.total > 100 must pass."""
    v = SQLValidator()
    v.validate(
        "SELECT u.name FROM users u JOIN orders o ON u.id = o.user_id WHERE o.total > 100",
        allowed_tables=ALLOWED_TABLES,
    )


def test_tdd_positive_cte():
    """RED→GREEN: WITH recent_sales AS (...) SELECT * FROM recent_sales must pass (CTEs)."""
    v = SQLValidator()
    v.validate(
        """
        WITH recent_sales AS (SELECT * FROM orders WHERE created_at > '2024-01-01')
        SELECT * FROM recent_sales
        """,
        allowed_tables=ALLOWED_TABLES,
    )


def test_tdd_refactor_aliasing_as():
    """REFACTOR: SELECT * FROM users AS u must resolve to users for scope check."""
    v = SQLValidator()
    v.validate("SELECT * FROM users AS u WHERE u.id = 1", allowed_tables=ALLOWED_TABLES)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator():
    """SQLValidator instance for mock tests."""
    return SQLValidator()


# ---------------------------------------------------------------------------
# Layer 1: Lexical Block (DML/DDL keywords)
# ---------------------------------------------------------------------------


def test_lexical_block_delete():
    """DELETE must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("DELETE FROM users")
    assert exc.value.layer == "lexical"
    assert "DELETE" in str(exc.value)


def test_lexical_block_drop():
    """DROP must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("DROP TABLE users")
    assert exc.value.layer == "lexical"


def test_lexical_block_update():
    """UPDATE must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("UPDATE orders SET status='shipped'")
    assert exc.value.layer == "lexical"


def test_lexical_block_insert():
    """INSERT must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("INSERT INTO logs (msg) VALUES ('test')")
    assert exc.value.layer == "lexical"


def test_lexical_block_alter():
    """ALTER must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("ALTER TABLE users ADD COLUMN x INT")
    assert exc.value.layer == "lexical"


def test_lexical_block_truncate():
    """TRUNCATE must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("TRUNCATE TABLE temp_data")
    assert exc.value.layer == "lexical"


def test_lexical_block_grant_revoke():
    """GRANT and REVOKE must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("GRANT SELECT ON db.* TO 'user'@'host'")
    with pytest.raises(SecurityViolation):
        v.validate("REVOKE EXECUTE ON PROCEDURE p FROM 'u'@'h'")


def test_lexical_block_exec():
    """EXEC/EXECUTE must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("EXEC dangerous_procedure")
    with pytest.raises(SecurityViolation):
        v.validate("EXECUTE IMMEDIATE 'DROP TABLE x'")


# ---------------------------------------------------------------------------
# Layer 1: String literal edge case (must PASS)
# ---------------------------------------------------------------------------


def test_string_literal_delete_passes():
    """WHERE comment = 'Please delete me' must PASS (delete is inside string, not keyword)."""
    v = SQLValidator()
    # Should not raise - 'delete' is inside string literal, not a keyword
    v.validate("SELECT id FROM comments WHERE comment = 'Please delete me'")


# ---------------------------------------------------------------------------
# Layer 2: Scope Check
# ---------------------------------------------------------------------------


def test_scope_check_allowed_tables():
    """Query referencing only allowed tables must pass."""
    v = SQLValidator()
    v.validate(
        "SELECT * FROM sales_data WHERE amount > 0",
        allowed_tables={"sales_data"},
    )


def test_scope_check_out_of_scope():
    """Query referencing secret_settings when only sales_data allowed must FAIL."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM secret_settings WHERE key = 'api_key'",
            allowed_tables={"sales_data"},
        )
    assert exc.value.layer == "scope"
    assert "secret_settings" in str(exc.value)


def test_scope_check_multiple_tables():
    """Query with JOIN: all tables must be in scope."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM sales_data s JOIN orders o ON s.id = o.sale_id",
            allowed_tables={"sales_data"},
        )
    assert exc.value.layer == "scope"
    assert "orders" in str(exc.value)


def test_scope_check_multiple_allowed():
    """Query with JOIN when both tables allowed must pass."""
    v = SQLValidator()
    v.validate(
        "SELECT * FROM sales_data s JOIN orders o ON s.id = o.sale_id",
        allowed_tables={"sales_data", "orders"},
    )


def test_scope_check_subquery():
    """Subquery tables must be in scope."""
    v = SQLValidator()
    v.validate(
        "SELECT * FROM sales WHERE id IN (SELECT sale_id FROM orders)",
        allowed_tables={"sales", "orders"},
    )


def test_scope_check_subquery_out_of_scope():
    """Subquery referencing out-of-scope table must FAIL."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM sales WHERE id IN (SELECT id FROM secret_users)",
            allowed_tables={"sales"},
        )
    assert exc.value.layer == "scope"


def test_is_in_scope_helper():
    """is_in_scope returns True only when all tables allowed."""
    v = SQLValidator()
    assert v.is_in_scope("SELECT * FROM sales", {"sales"}) is True
    assert v.is_in_scope("SELECT * FROM sales", {"sales", "orders"}) is True
    assert v.is_in_scope("SELECT * FROM sales JOIN orders ON 1", {"sales", "orders"}) is True
    assert v.is_in_scope("SELECT * FROM secret", {"sales"}) is False


# ---------------------------------------------------------------------------
# Layer 3: Injection Shield
# ---------------------------------------------------------------------------


def test_injection_piggyback():
    """SELECT * FROM sales; DROP TABLE users must FAIL."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("SELECT * FROM sales; DROP TABLE users")
    assert exc.value.layer == "injection"


def test_injection_multi_statement():
    """Multiple statements separated by ; must FAIL."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("SELECT 1; SELECT 2")
    assert exc.value.layer == "injection"


def test_injection_semicolon_in_string():
    """Semicolon inside string literal must not trigger injection block."""
    v = SQLValidator()
    # "SELECT * FROM t WHERE c = 'a;b'" - single statement, ; is in string
    v.validate("SELECT * FROM t WHERE c = 'a;b'")


# ---------------------------------------------------------------------------
# Literature-backed: DoS, Info Disclosure, Tautology, Prompt Injection
# ---------------------------------------------------------------------------


def test_dos_benchmark_blocked():
    """Peng et al.: benchmark() DoS must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT benchmark(1000000000, (SELECT database()))",
            allowed_tables=set(),
        )
    assert "benchmark" in str(exc.value).lower() or "function" in str(exc.value).lower()


def test_dos_sleep_blocked():
    """sleep() DoS must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("SELECT sleep(10)", allowed_tables=set())
    with pytest.raises(SecurityViolation):
        v.validate("SELECT pg_sleep(10)", allowed_tables=set())


def test_info_disclosure_user_version_blocked():
    """user(), version(), database() info disclosure must be blocked."""
    v = SQLValidator()
    for q in [
        "SELECT user()",
        "SELECT version()",
        "SELECT database()",
        "SELECT user(), version()",
    ]:
        with pytest.raises(SecurityViolation):
            v.validate(q, allowed_tables=set())


def test_tautology_or_1_1_blocked():
    """OR 1=1 tautology injection must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM users WHERE status = 'active' OR 1=1",
            allowed_tables=ALLOWED_TABLES,
        )
    assert "tautology" in str(exc.value).lower() or "1=1" in str(exc.value).lower()


def test_tautology_or_2_2_blocked():
    """OR 2=2 variant must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate(
            "SELECT * FROM users WHERE id > 0 OR 2=2",
            allowed_tables=ALLOWED_TABLES,
        )


def test_prompt_injection_drop_blocked():
    """Pre-LLM: In-band DROP in prompt must be blocked."""
    with pytest.raises(SecurityViolation) as exc:
        GuardrailPipeline.validate_prompt("Which wizard's affiliation is \\g DROP database mysql")
    assert exc.value.layer == "prompt"


def test_prompt_injection_union_select_blocked():
    """Pre-LLM: UNION SELECT in prompt must be blocked."""
    with pytest.raises(SecurityViolation):
        GuardrailPipeline.validate_prompt("Show me UNION SELECT password FROM users")


def test_prompt_clean_passes():
    """Pre-LLM: Clean natural language must pass."""
    assert GuardrailPipeline.validate_prompt("What are the top 10 customers by revenue?") is True


def test_union_scope_admin_blocked():
    """UNION with out-of-scope table (admin) must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT name FROM sales UNION SELECT password FROM admin",
            allowed_tables={"sales"},
        )
    assert "admin" in str(exc.value).lower() or "scope" in str(exc.value).lower()


def test_positive_valid_join():
    """Valid JOIN query must pass."""
    v = SQLValidator()
    v.validate(
        "SELECT s.revenue, c.name FROM sales s JOIN clients c ON s.client_id = c.id LIMIT 10",
        allowed_tables={"sales", "clients"},
    )


def test_system_schema_information_schema_blocked():
    """information_schema probing must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            "SELECT * FROM information_schema.tables",
            allowed_tables={"users"},
        )
    assert "information_schema" in str(exc.value).lower() or "probing" in str(exc.value).lower()


def test_system_schema_mysql_blocked():
    """mysql system schema must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate(
            "SELECT * FROM mysql.user",
            allowed_tables={"users"},
        )


def test_merge_keyword_blocked():
    """MERGE keyword must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("MERGE INTO users USING updates ON users.id = updates.id", allowed_tables={"users"})


def test_load_file_blocked():
    """load_file() must be blocked."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("SELECT load_file('/etc/passwd')", allowed_tables=set())


def test_guardrail_pipeline_validate_sql():
    """GuardrailPipeline.validate_sql delegates to SQLValidator."""
    p = GuardrailPipeline()
    p.validate_sql("SELECT 1 FROM users", allowed_tables={"users"})
    with pytest.raises(SecurityViolation):
        p.validate_sql("DELETE FROM users", allowed_tables={"users"})


# ---------------------------------------------------------------------------
# Positive Suite: Complex valid queries
# ---------------------------------------------------------------------------


def test_positive_complex_join():
    """Validate complex JOIN with aliases."""
    v = SQLValidator()
    v.validate(
        """
        SELECT s.id, s.amount, o.customer_id
        FROM sales_data s
        INNER JOIN orders o ON s.order_id = o.id
        WHERE s.amount > 100
        """,
        allowed_tables={"sales_data", "orders"},
    )


def test_positive_group_by():
    """Validate GROUP BY query."""
    v = SQLValidator()
    v.validate(
        "SELECT region, SUM(amount) FROM sales GROUP BY region",
        allowed_tables={"sales"},
    )


def test_positive_cte():
    """Validate CTE (WITH clause)."""
    v = SQLValidator()
    v.validate(
        """
        WITH cte AS (SELECT id, name FROM users WHERE active = 1)
        SELECT * FROM cte
        """,
        allowed_tables={"users", "cte"},
    )


def test_positive_aliases():
    """Validate queries with table aliases."""
    v = SQLValidator()
    v.validate(
        "SELECT a.id, b.total FROM orders a JOIN sales b ON a.sale_id = b.id",
        allowed_tables={"orders", "sales"},
    )


def test_positive_subquery():
    """Validate subquery in WHERE."""
    v = SQLValidator()
    v.validate(
        "SELECT * FROM products WHERE category_id IN (SELECT id FROM categories)",
        allowed_tables={"products", "categories"},
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_query():
    """Empty query must fail."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation) as exc:
        v.validate("")
    assert exc.value.layer == "lexical"


def test_whitespace_only():
    """Whitespace-only query must fail."""
    v = SQLValidator()
    with pytest.raises(SecurityViolation):
        v.validate("   \n\t  ")


def test_scope_none_skips_check():
    """When allowed_tables is None, scope check is skipped."""
    v = SQLValidator()
    v.validate("SELECT * FROM any_table")


# ---------------------------------------------------------------------------
# validate_and_execute
# ---------------------------------------------------------------------------


def test_validate_and_execute_passes():
    """validate_and_execute runs execute_fn when valid."""
    v = SQLValidator()
    calls = []

    def exec_fn(sql):
        calls.append(sql)
        return "ok"

    result = v.validate_and_execute(
        exec_fn,
        "SELECT 1 FROM sales",
        allowed_tables={"sales"},
    )
    assert result == "ok"
    assert len(calls) == 1
    assert "SELECT 1" in calls[0]


def test_validate_and_execute_blocks():
    """validate_and_execute does not call execute_fn when invalid."""
    v = SQLValidator()
    calls = []

    def exec_fn(sql):
        calls.append(sql)
        return "ok"

    with pytest.raises(SecurityViolation):
        v.validate_and_execute(exec_fn, "DELETE FROM users")

    assert len(calls) == 0


# ---------------------------------------------------------------------------
# Live tests (real MySQL when configured)
# ---------------------------------------------------------------------------


def _get_mysql_config():
    """
    Get MySQL config for live tests.
    Prefer MYSQL_* env vars; else use first database from daibai config (daibai.yaml + .env).
    """
    # Explicit test env vars
    if os.environ.get("MYSQL_HOST") or os.environ.get("MYSQL_PASSWORD"):
        return (
            os.environ.get("MYSQL_HOST", "localhost"),
            int(os.environ.get("MYSQL_PORT", "3306")),
            os.environ.get("MYSQL_USER", "root"),
            os.environ.get("MYSQL_PASSWORD", ""),
            os.environ.get("MYSQL_DATABASE", "test"),
        )
    # Fallback: daibai config (loads daibai.yaml + .env)
    try:
        from pathlib import Path

        from daibai.core.config import load_config

        cfg = load_config()
        if cfg.databases:
            db = cfg.get_database(cfg.default_database or list(cfg.databases.keys())[0])
            return (db.host, db.port, db.user, db.password or "", db.database)
    except Exception:
        pass
    return ("localhost", 3306, "root", "", "test")


def _has_mysql():
    """True if MySQL is configured for live tests (MYSQL_* or daibai config)."""
    _, _, _, password, _ = _get_mysql_config()
    return bool(password)


def _live_exec_fn():
    """Return execute_fn that runs SQL against live MySQL."""
    import mysql.connector

    host, port, user, password, database = _get_mysql_config()

    def exec_fn(sql):
        conn = mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
        )
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            if cursor.description:
                return cursor.fetchall()
            conn.commit()
            return []
        finally:
            conn.close()

    return exec_fn


def _live_conn():
    """Return live MySQL connection for setup/teardown."""
    import mysql.connector

    host, port, user, password, database = _get_mysql_config()
    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


@pytest.fixture(scope="module")
def live_test_table():
    """
    Create a temporary test table for live guardrail tests.
    Yields table name; drops table on teardown. Skips if CREATE not allowed.
    """
    if not _has_mysql():
        pytest.skip("MYSQL_HOST/MYSQL_PASSWORD not set - live guardrail fixture requires MySQL")
    table = "_daibai_guardrail_test"
    try:
        conn = _live_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            cursor.execute(
                f"""
                CREATE TABLE `{table}` (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(100),
                    amount DECIMAL(10,2)
                )
                """
            )
            conn.commit()
            yield table
        finally:
            try:
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
                conn.commit()
            except Exception:
                pass
            conn.close()
    except Exception as e:
        pytest.skip(f"Cannot create live test table: {e}")


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_validate_and_execute_live():
    """
    Validate then execute against real MySQL.
    Ensures guardrail blocks bad queries before they reach the DB.
    """
    exec_fn = _live_exec_fn()
    v = SQLValidator()

    result = v.validate_and_execute(exec_fn, "SELECT 1 AS test", allowed_tables=set())
    assert result is not None

    with pytest.raises(SecurityViolation) as exc:
        v.validate_and_execute(exec_fn, "DELETE FROM nonexistent_table")
    assert exc.value.layer == "lexical"

    with pytest.raises(SecurityViolation) as exc:
        v.validate_and_execute(exec_fn, "SELECT 1; DROP TABLE nonexistent")
    assert exc.value.layer == "injection"


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_lexical_block_all_dml_live():
    """
    Each forbidden DML/DDL keyword is blocked before reaching real MySQL.
    """
    exec_fn = _live_exec_fn()
    v = SQLValidator()

    blocked = [
        "DELETE FROM x",
        "DROP TABLE x",
        "UPDATE x SET y=1",
        "INSERT INTO x (y) VALUES (1)",
        "ALTER TABLE x ADD COLUMN y INT",
        "TRUNCATE TABLE x",
        "GRANT SELECT ON db.* TO 'u'@'h'",
        "REVOKE SELECT ON db.* FROM 'u'@'h'",
    ]
    for sql in blocked:
        with pytest.raises(SecurityViolation) as exc:
            v.validate_and_execute(exec_fn, sql)
        assert exc.value.layer == "lexical", f"Expected lexical block for: {sql[:40]}"


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_injection_blocked_live():
    """
    Multi-statement injection is blocked before reaching real MySQL.
    """
    exec_fn = _live_exec_fn()
    v = SQLValidator()

    injections = [
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE x",
        "SELECT 1; DELETE FROM x",
        "SELECT 1; INSERT INTO x VALUES (1)",
    ]
    for sql in injections:
        with pytest.raises(SecurityViolation) as exc:
            v.validate_and_execute(exec_fn, sql)
        assert exc.value.layer == "injection", f"Expected injection block for: {sql[:40]}"


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
@pytest.mark.skip(reason="Temporarily skipped: live DB unstable")
def test_positive_select_live(live_test_table):
    """
    Valid SELECT against real table returns data through validate_and_execute.
    """
    exec_fn = _live_exec_fn()
    v = SQLValidator()

    # Insert via raw connection (bypass validator for setup)
    conn = _live_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f"INSERT INTO `{live_test_table}` (name, amount) VALUES ('a', 10.5)")
        conn.commit()
    finally:
        conn.close()

    result = v.validate_and_execute(
        exec_fn,
        f"SELECT id, name, amount FROM `{live_test_table}`",
        allowed_tables={live_test_table},
    )
    assert result is not None
    assert len(result) >= 1
    assert "name" in result[0] or "amount" in result[0]


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_string_literal_passes_live(live_test_table):
    """
    Query with 'delete' inside string literal passes against real MySQL.
    """
    exec_fn = _live_exec_fn()
    v = SQLValidator()

    result = v.validate_and_execute(
        exec_fn,
        f"SELECT 1 AS x FROM `{live_test_table}` WHERE name = 'Please delete me' LIMIT 1",
        allowed_tables={live_test_table},
    )
    assert result is not None


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_scope_check_live():
    """
    Scope check against real MySQL: query allowed table passes, out-of-scope fails.
    """
    host, port, user, password, database = _get_mysql_config()
    v = SQLValidator()

    conn = _live_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s LIMIT 5",
            (database,),
        )
        tables = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    if not tables:
        pytest.skip("No tables in database for scope test")

    allowed = {tables[0].lower()}
    v.validate(f"SELECT * FROM `{tables[0]}` LIMIT 1", allowed_tables=allowed)

    out_of_scope = "nonexistent_table_xyz_123"
    with pytest.raises(SecurityViolation) as exc:
        v.validate(f"SELECT * FROM {out_of_scope}", allowed_tables=allowed)
    assert exc.value.layer == "scope"


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_scope_join_live(live_test_table):
    """
    Scope check with JOIN: both tables must be allowed.
    """
    v = SQLValidator()
    exec_fn = _live_exec_fn()

    # Single table in scope - passes
    result = v.validate_and_execute(
        exec_fn,
        f"SELECT * FROM `{live_test_table}` LIMIT 1",
        allowed_tables={live_test_table},
    )
    assert result is not None

    # JOIN with out-of-scope table - fails
    with pytest.raises(SecurityViolation) as exc:
        v.validate(
            f"SELECT * FROM `{live_test_table}` t1 JOIN information_schema.tables t2 ON 1=1",
            allowed_tables={live_test_table},
        )
    assert exc.value.layer == "scope"


@pytest.mark.cloud
@pytest.mark.skipif(not _has_mysql(), reason="MYSQL_* not set - live test requires MySQL")
def test_agent_run_sql_live(live_test_table):
    """
    Full pipeline: Agent with real MySQL config runs valid SELECT through guardrail.
    """
    from pathlib import Path

    from daibai.core.agent import DaiBaiAgent
    from daibai.core.config import Config, DatabaseConfig

    host, port, user, password, database = _get_mysql_config()
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={
            "test": DatabaseConfig(
                name="test",
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
        },
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    # Valid SELECT must pass and return data
    df = agent.run_sql(
        f"SELECT * FROM `{live_test_table}` LIMIT 1",
        allowed_tables={live_test_table},
    )
    assert df is not None

    # DML must be blocked
    with pytest.raises(SecurityViolation):
        agent.run_sql(f"DELETE FROM `{live_test_table}`")


def test_agent_run_sql_blocks_dml():
    """
    Agent.run_sql blocks DML before execution (integration with guardrails).
    """
    from pathlib import Path

    from daibai.core.agent import DaiBaiAgent
    from daibai.core.config import Config, DatabaseConfig
    from daibai.core.guardrails import SecurityViolation

    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={
            "test": DatabaseConfig(
                name="test",
                host="localhost",
                port=3306,
                database="test",
                user="u",
                password="p",
            )
        },
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    with pytest.raises(SecurityViolation) as exc:
        agent.run_sql("DELETE FROM users")
    assert exc.value.layer == "lexical"

    with pytest.raises(SecurityViolation) as exc:
        agent.run_sql("SELECT 1; DROP TABLE x")
    assert exc.value.layer == "injection"


def test_agent_generate_sql_blocks_malicious_prompt():
    """
    Agent.generate_sql raises SecurityViolation for prompts with in-band SQL injection.
    """
    from pathlib import Path

    from daibai.core.agent import DaiBaiAgent
    from daibai.core.config import Config, DatabaseConfig

    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={
            "test": DatabaseConfig(
                name="test",
                host="localhost",
                port=3306,
                database="test",
                user="u",
                password="p",
            )
        },
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    agent = DaiBaiAgent(config=config, auto_train=False)

    with pytest.raises(SecurityViolation) as exc:
        agent.generate_sql("Show me UNION SELECT password FROM users")
    assert exc.value.layer == "prompt"

    with pytest.raises(SecurityViolation) as exc:
        agent.generate_sql("Which wizard's affiliation is \\g DROP database mysql")
    assert exc.value.layer == "prompt"


@pytest.mark.cloud
@pytest.mark.skipif(
    not _has_mysql(),
    reason="MYSQL_HOST/MYSQL_PASSWORD not set - live scope test requires MySQL",
)
def test_scope_check_live():
    """
    Scope check against real MySQL: query allowed table passes, out-of-scope fails.
    """
    import mysql.connector

    host, port, user, password, database = _get_mysql_config()
    v = SQLValidator()

    # Get actual tables from information_schema
    conn = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s LIMIT 5",
            (database,),
        )
        tables = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    if not tables:
        pytest.skip("No tables in database for scope test")

    allowed = {tables[0]}
    v.validate(f"SELECT * FROM `{tables[0]}` LIMIT 1", allowed_tables=allowed)

    # Query out-of-scope table must fail
    out_of_scope = "nonexistent_table_xyz_123"
    with pytest.raises(SecurityViolation) as exc:
        v.validate(f"SELECT * FROM {out_of_scope}", allowed_tables=allowed)
    assert exc.value.layer == "scope"
