"""Tests for SchemaPruningMetrics."""

import json
import tempfile
from pathlib import Path

import pytest

from daibai.core.metrics import SchemaPruningMetrics


@pytest.fixture
def metrics_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def metrics(metrics_dir):
    return SchemaPruningMetrics(metrics_dir)


def test_record_success_and_get_stats(metrics):
    """Recording successes populates stats."""
    metrics.record_success(tables_in_context=5, tables_in_query=3)
    metrics.record_success(tables_in_context=5, tables_in_query=4)
    metrics.record_success(tables_in_context=5, tables_in_query=2)

    stats = metrics.get_stats()
    assert stats["sample_count"] == 3
    assert stats["avg_tables_in_query"] == 3.0
    assert stats["p95_tables_in_query"] == 4
    assert stats["max_tables_in_query"] == 4
    assert stats["scope_violations"] == 0
    assert stats["suggested_limit"] is not None


def test_record_scope_violation(metrics):
    """Scope violations are counted."""
    metrics.record_scope_violation()
    metrics.record_scope_violation()
    stats = metrics.get_stats()
    assert stats["scope_violations"] == 2


def test_empty_stats(metrics):
    """No data returns zeros."""
    stats = metrics.get_stats()
    assert stats["sample_count"] == 0
    assert stats["avg_tables_in_query"] == 0
    assert stats["suggested_limit"] is None


def test_suggested_limit_based_on_p95(metrics):
    """Suggested limit is p95 + headroom."""
    for _ in range(20):
        metrics.record_success(tables_in_context=10, tables_in_query=8)
    stats = metrics.get_stats()
    assert stats["p95_tables_in_query"] == 8
    assert stats["suggested_limit"] == 10  # 8 + 2 headroom
