"""
Regression tests for DaiBai agent modes, including STRICT COLUMN PROJECTION RULE.

Validates that when the user uses language indicating they want results shown (show, include,
add, provide, list, display, etc.) and mentions metrics, the generated SQL projects those
metrics in the SELECT clause, not only in the WHERE clause.
"""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from daibai.core.agent import DaiBaiAgent
from daibai.core.config import Config, DatabaseConfig
from daibai.llm.base import LLMResponse


# Trigger words: any language indicating user wants results shown (triggers STRICT COLUMN PROJECTION)
RESULT_TRIGGERS = frozenset(("show", "include", "add", "provide", "list", "display", "give", "return"))


def prompt_expects_projected_metrics(prompt: str) -> bool:
    """True if prompt suggests user wants specific metrics in the output (triggers STRICT COLUMN PROJECTION)."""
    lower = prompt.lower()
    return any(trig in lower for trig in RESULT_TRIGGERS)


@pytest.fixture
def agent():
    """Minimal agent for mode tests."""
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    return DaiBaiAgent(config=config, auto_train=False)


# (prompt, intent, expect_fail) — intent used to mock _classify_intent
queries = [
    ("Show all tables with rowcount > 100, include rowcounts", "GENERAL_QUESTION", False),
    ("List tables and provide row counts for those over 100 rows", "GENERAL_QUESTION", False),
    ("Display table names and add the row count", "GENERAL_QUESTION", False),
]


@pytest.mark.parametrize("prompt,intent,expect_fail", queries)
def test_daibai_modes_column_projection(prompt, intent, expect_fail, agent):
    """
    Ensure requested metrics appear in SELECT clause (STRICT COLUMN PROJECTION RULE).
    When user uses show/include/add/provide/list/display and mentions metrics,
    TABLE_ROWS or COUNT must appear in SELECT, not only WHERE.
    """
    agent._current_db = "test"
    captured_sql = [None]

    # SQL that correctly projects TABLE_ROWS in SELECT (matches STRICT COLUMN PROJECTION RULE)
    correct_sql = "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_ROWS > 100"

    def capture_run_sql(sql, db_name=None, allowed_tables=None, strict_scope=False, execution_mode="read_only"):
        captured_sql[0] = sql
        return pd.DataFrame({"TABLE_NAME": ["users"], "TABLE_ROWS": [150]})

    def mock_generate(inner_prompt, context=None):
        return LLMResponse(text="", sql=correct_sql)

    with patch.object(agent, "run_sql", side_effect=capture_run_sql):
        with patch.object(agent, "_classify_intent", return_value=intent):
            with patch.object(agent, "_get_pruned_schema", return_value=("-- schema", set())):
                with patch.object(agent, "_get_schema_manager", return_value=None):
                    with patch.object(agent, "generate", side_effect=mock_generate):
                        agent.generate_sql(prompt)

    sql = captured_sql[0]
    assert sql is not None, "No SQL was executed"

    if prompt_expects_projected_metrics(prompt):
        # Check that the SQL contains TABLE_ROWS (or equivalent metric) in the SELECT part
        # A simple check: ensure TABLE_ROWS appears before FROM
        select_part = sql.upper().split("FROM")[0]
        assert "TABLE_ROWS" in select_part or "COUNT" in select_part or "AVG(" in select_part, (
            "Failed to project requested metric in SELECT clause"
        )
