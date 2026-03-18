"""
Tests for the Agentic Reflection Loop (Phase 2/3).

Verifies that _execute_with_reflection catches SQL execution errors,
asks the LLM to repair the SQL, and retries successfully.
"""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from daibai.core.agent import DaiBaiAgent
from daibai.core.config import Config, DatabaseConfig
from daibai.llm.base import LLMResponse


@pytest.fixture
def agent():
    """Minimal agent for reflection loop tests."""
    config = Config(
        default_database="test",
        default_llm="gemini",
        databases={"test": DatabaseConfig("test", "localhost", 3306, "test", "u", "p")},
        llm_providers={},
        memory_dir=Path("/tmp"),
    )
    return DaiBaiAgent(config=config, auto_train=False)


def test_execute_with_reflection_retries_on_derived_table_alias_error(agent):
    """
    When run_sql raises 'Every derived table must have its own alias' on first call,
    the reflection loop asks the LLM to fix the SQL, then retries.
    Second call returns a valid DataFrame; final output matches the result.
    """
    # SQL without alias (will fail in MySQL)
    bad_sql = "SELECT COUNT(*) AS count FROM (SELECT id FROM users)"
    # Repaired SQL with alias
    repaired_sql = "SELECT COUNT(*) AS count FROM (SELECT id FROM users) AS sub"
    # Valid result on retry
    expected_df = pd.DataFrame({"count": [5]})

    agent._current_db = "test"
    agent._last_allowed_tables = set()
    agent._last_sanitized_query = "how many users"

    # 1st call: raise MySQL derived-table error; 2nd call: return DataFrame
    run_sql_side_effects = [
        Exception("1248 (42000): Every derived table must have its own alias"),
        expected_df,
    ]

    # LLM returns repaired SQL when asked to fix
    def mock_generate(prompt, context=None):
        return LLMResponse(text="", sql=repaired_sql)

    with patch.object(agent, "run_sql", side_effect=run_sql_side_effects) as mock_run_sql:
        with patch.object(agent, "_get_pruned_schema", return_value=("-- Table: users\n  id int", {"users"})):
            with patch.object(agent, "generate", side_effect=mock_generate) as mock_gen:
                result = agent._execute_with_reflection(
                    original_prompt="how many users",
                    generated_sql=bad_sql,
                    intent="READ_DATA",
                    max_retries=2,
                )

    # Result should contain the DataFrame output (proves retry succeeded)
    assert "count" in result
    assert "5" in result
    assert "**Source Query:**" in result
    assert "```sql" in result

    # run_sql was called twice: first failed, second succeeded
    assert mock_run_sql.call_count == 2

    # generate was called once (for the repair prompt)
    assert mock_gen.call_count == 1
