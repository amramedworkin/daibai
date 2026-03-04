#!/usr/bin/env python3
"""
DaiBai Semantic Schema Indexer
================================
Vectorises a database schema and stores the result in Redis so that the AI
can prune irrelevant tables before generating SQL (semantic table-pruning).

Usage
-----
    python scripts/index_db.py [target] [--force]

    target   playground        Index the Chinook SQLite playground (default).
             <db-name>         Index a named database from daibai.yaml.

    --force  Re-index even if SCHEMA_REFRESH_INTERVAL has not elapsed.

Examples
--------
    python scripts/index_db.py
    python scripts/index_db.py playground
    python scripts/index_db.py my_production_db
    python scripts/index_db.py playground --force

How it works
------------
1. Playground target
   • Reads column metadata from data/playground.db (SQLite) via PRAGMA table_info.
   • Auto-creates playground.db from chinook_master.db if it is missing.
   • Passes a custom execute_fn to SchemaManager so the standard MySQL
     information_schema path is bypassed.

2. Named-database target
   • Reads the DatabaseConfig from daibai.yaml for the named database.
   • Connects via mysql-connector-python (same as the main app).

In both cases SchemaManager.index_schema() handles embedding + Redis storage
using the schema:v1:* key namespace expected by search_schema_v1() / the WS
/ws/schema-progress endpoint.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Silence noisy-but-harmless model-loader output ──────────────────────────
# 1. Suppresses "position_ids UNEXPECTED" BertModel LOAD REPORT from transformers.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
logging.getLogger("transformers").setLevel(logging.ERROR)
# 2. Suppresses the "Loading weights: 100%|..." tqdm bar from sentence-transformers.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# 3. Suppresses "unauthenticated requests to HF Hub" advisory from huggingface_hub.
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Bootstrap: make the daibai package importable when the script is run
# directly (e.g. "python scripts/index_db.py") from any working directory.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from daibai.core.cache import CacheManager
from daibai.core.config import get_redis_connection_string
from daibai.core.schema import SchemaManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DATA_DIR   = _ROOT / "data"
_MASTER_DB  = _DATA_DIR / "chinook_master.db"
_PLAY_DB    = _DATA_DIR / "playground.db"


# ---------------------------------------------------------------------------
# SQLite execute_fn adapter
# ---------------------------------------------------------------------------

def _build_sqlite_execute_fn(db_path: Path):
    """
    Return an execute_fn compatible with SchemaManager that queries a SQLite
    database and returns rows shaped like MySQL's information_schema.COLUMNS.

    SchemaManager calls execute_fn(sql, params) where sql is the standard
    information_schema query and params is (schema_name,).  We ignore the SQL
    entirely and talk to SQLite instead.
    """
    def _execute_fn(sql: str, params: tuple) -> List[Dict[str, Any]]:
        schema_name = params[0] if params else "playground"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Fetch table names (excludes SQLite internal tables).
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        table_names = [r["name"] for r in cur.fetchall()]

        rows: List[Dict[str, Any]] = []
        for table_name in table_names:
            cur.execute(f"PRAGMA table_info('{table_name}')")
            for col in cur.fetchall():
                col_type = (col["type"] or "TEXT").upper()
                rows.append({
                    "TABLE_SCHEMA":  schema_name,
                    "TABLE_NAME":    table_name,
                    "COLUMN_NAME":   col["name"],
                    "DATA_TYPE":     col_type,
                    "COLUMN_TYPE":   col_type,
                    "IS_NULLABLE":   "NO" if col["notnull"] else "YES",
                    "COLUMN_KEY":    "PRI" if col["pk"] else "",
                    "COLUMN_DEFAULT": col["dflt_value"],
                    "EXTRA":         "",
                })

        conn.close()
        return rows

    return _execute_fn


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

def _make_progress_cb(total_hint: Optional[int] = None):
    """
    Return a progress_cb(pct, status, eta) suitable for the terminal.
    SchemaManager calls it with (pct: float 0-100, status: str, eta: float seconds).
    """
    def _cb(pct: float, status: str, eta: float) -> None:
        bar_len = 28
        filled  = int(bar_len * pct / 100)
        bar     = "█" * filled + "░" * (bar_len - filled)
        eta_str = f"  ETA {eta:4.0f}s" if eta > 0.5 else "         "
        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  {status:<32}{eta_str}"
        )
        sys.stdout.flush()

    return _cb


# ---------------------------------------------------------------------------
# Indexing routines
# ---------------------------------------------------------------------------

def _get_cache() -> Optional[CacheManager]:
    """Build a CacheManager from the environment, or return None with a message."""
    redis_url = get_redis_connection_string()
    if not redis_url:
        print(
            "  ERROR: No Redis connection string found.\n"
            "         Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env, or run:\n"
            "           ./scripts/cli.sh redis-create",
            file=sys.stderr,
        )
        return None
    return CacheManager(connection_string=redis_url)


def index_playground(schema_name: str = "playground", *, force: bool = False) -> int:
    """
    Vectorise the Chinook SQLite playground schema and store in Redis.

    Returns the number of tables successfully indexed.
    """
    # Auto-create playground.db from master if absent.
    if not _PLAY_DB.exists():
        if not _MASTER_DB.exists():
            print(
                f"  ERROR: chinook_master.db not found at {_MASTER_DB}.\n"
                "         Place the file in data/ and try again.",
                file=sys.stderr,
            )
            return 0
        print(f"  playground.db missing — copying from chinook_master.db …")
        shutil.copy2(_MASTER_DB, _PLAY_DB)
        print(f"  Created: {_PLAY_DB}")

    logger.info("[index] playground: start (force=%s)", force)

    cache = _get_cache()
    if cache is None:
        logger.warning("[index] playground: skipped — no cache")
        return 0

    sm = SchemaManager(
        config=None,
        execute_fn=_build_sqlite_execute_fn(_PLAY_DB),
        cache_manager=cache,
    )

    n = sm.index_schema(
        schema_name=schema_name,
        force=force,
        progress_cb=_make_progress_cb(),
    )
    logger.info("[index] playground: done — %d table(s)", n)
    return n


def index_named_db(db_name: str, *, force: bool = False) -> int:
    """
    Vectorise a production database configured in daibai.yaml.

    Returns the number of tables successfully indexed.
    """
    try:
        from daibai.core.config import settings
        db_config = settings.get_database(db_name)
    except ValueError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"  ERROR: Could not load daibai.yaml — {e}", file=sys.stderr)
        logger.warning("[index] %s: config load failed — %s", db_name, e)
        return 0

    logger.info("[index] %s: start (force=%s)", db_name, force)

    cache = _get_cache()
    if cache is None:
        logger.warning("[index] %s: skipped — no cache", db_name)
        return 0

    sm = SchemaManager(config=db_config, cache_manager=cache)

    n = sm.index_schema(
        schema_name=db_name,
        force=force,
        progress_cb=_make_progress_cb(),
    )
    logger.info("[index] %s: done — %d table(s)", db_name, n)
    return n


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args   = sys.argv[1:]
    target = next((a for a in args if not a.startswith("-")), "playground")
    force  = "--force" in args or "-f" in args

    print()
    print("=" * 62)
    print(f"  DaiBai Semantic Schema Indexer")
    print(f"  Target  : {target}")
    print(f"  Force   : {'yes (bypassing refresh interval)' if force else 'no'}")
    print("=" * 62)
    print()

    if target == "playground":
        print(f"  Source  : data/playground.db  (Chinook SQLite)")
        print(f"  Redis   : schema:v1:* keys  (schema_name='playground')")
    else:
        print(f"  Source  : {target}  (from daibai.yaml)")
        print(f"  Redis   : schema:v1:* keys  (schema_name='{target}')")
    print()

    t0 = time.monotonic()

    count = (
        index_playground("playground", force=force)
        if target == "playground"
        else index_named_db(target, force=force)
    )

    elapsed = time.monotonic() - t0

    # Final newline after the progress bar overwrites.
    print()
    print()

    if count > 0:
        print(f"  ✓  Indexed {count} table(s) in {elapsed:.1f}s")
        print()
        print(f"  The AI will use semantic table-pruning for '{target}'.")
        print(f"  Vectors expire after 24 h (Redis TTL). Re-run to refresh.")
    else:
        print("  ✗  No tables were indexed.")
        print()
        print("  Common causes:")
        print("   • Redis unreachable — verify REDIS_URL in .env")
        print("     Run:  ./scripts/cli.sh cache-test")
        print("   • Embedding model unavailable — check OPENAI_API_KEY (or")
        print("     whichever provider supplies embeddings in daibai.yaml)")
        print("   • Schema already up-to-date — re-run with --force to override")
        if target == "playground":
            print("   • playground.db missing — run: ./scripts/cli.sh reset-sandbox")
        print()
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
