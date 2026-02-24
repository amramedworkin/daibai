"""
Usage metrics for schema pruning (Phase 3 Step 2).

Tracks table depth and scope of executed queries over time to inform
SCHEMA_VECTOR_LIMIT tuning. Persists to JSON in memory_dir.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


METRICS_FILE = "schema_pruning_metrics.json"
MAX_HISTORY = 500  # Keep last N successful queries
MAX_DAYS = 90  # Ignore entries older than this


class SchemaPruningMetrics:
    """
    Tracks schema pruning usage: tables in context vs tables in query.
    Use get_stats() for averages and suggested_limit for tuning.
    """

    def __init__(self, metrics_dir: Path):
        self._dir = Path(metrics_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / METRICS_FILE

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {
                "history": [],
                "scope_violations": 0,
                "updated_at": None,
            }
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"history": [], "scope_violations": 0, "updated_at": None}

    def _save(self, data: Dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def record_success(
        self,
        tables_in_context: int,
        tables_in_query: int,
    ) -> None:
        """
        Record a successful query execution.
        tables_in_context: len(allowed_tables) from pruned context.
        tables_in_query: number of tables referenced in the SQL.
        """
        data = self._load()
        history: List[Dict[str, Any]] = data.get("history", [])

        entry = {
            "tables_in_context": tables_in_context,
            "tables_in_query": tables_in_query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history.append(entry)

        # Trim to max size and drop very old entries
        cutoff = datetime.now(timezone.utc).timestamp() - (MAX_DAYS * 86400)
        history = [
            h for h in history[-MAX_HISTORY:]
            if datetime.fromisoformat(h["timestamp"]).timestamp() > cutoff
        ]
        data["history"] = history
        self._save(data)

    def record_scope_violation(self) -> None:
        """Record a SecurityViolation due to out-of-scope table (limit may be too low)."""
        data = self._load()
        data["scope_violations"] = data.get("scope_violations", 0) + 1
        self._save(data)

    def get_stats(self) -> Dict[str, Any]:
        """
        Return aggregate stats for tuning SCHEMA_VECTOR_LIMIT.
        """
        data = self._load()
        history: List[Dict[str, Any]] = data.get("history", [])

        if not history:
            return {
                "sample_count": 0,
                "avg_tables_in_query": 0,
                "p95_tables_in_query": 0,
                "max_tables_in_query": 0,
                "avg_tables_in_context": 0,
                "scope_violations": data.get("scope_violations", 0),
                "suggested_limit": None,
                "updated_at": data.get("updated_at"),
            }

        tables_in_query = [h["tables_in_query"] for h in history]
        tables_in_context = [h["tables_in_context"] for h in history]

        n = len(tables_in_query)
        avg_query = sum(tables_in_query) / n
        avg_context = sum(tables_in_context) / n
        sorted_query = sorted(tables_in_query)
        p95_idx = int(n * 0.95) if n > 0 else 0
        p95 = sorted_query[p95_idx] if sorted_query else 0
        max_query = max(tables_in_query) if tables_in_query else 0

        # Suggest limit: p95 + headroom (2), clamped 1–20
        suggested = min(20, max(1, int(p95) + 2)) if p95 else None

        return {
            "sample_count": n,
            "avg_tables_in_query": round(avg_query, 2),
            "p95_tables_in_query": p95,
            "max_tables_in_query": max_query,
            "avg_tables_in_context": round(avg_context, 2),
            "scope_violations": data.get("scope_violations", 0),
            "suggested_limit": suggested,
            "updated_at": data.get("updated_at"),
        }
