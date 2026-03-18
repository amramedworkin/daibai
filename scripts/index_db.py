#!/usr/bin/env python3
"""
DaiBai Semantic Schema Indexer
================================
Vectorises a database schema and stores the result in Redis so that the AI
can prune irrelevant tables before generating SQL (semantic table-pruning).

Usage
-----
    python scripts/index_db.py <db-name> [--force]

    <db-name>  Index a named database from daibai.yaml.

    --force    Re-index even if SCHEMA_REFRESH_INTERVAL has not elapsed.

Examples
--------
    python scripts/index_db.py my_production_db
    python scripts/index_db.py my_db --force

How it works
------------
Reads the DatabaseConfig from daibai.yaml for the named database.
Connects via mysql-connector-python.

SchemaManager.index_schema() handles embedding + Redis storage using the
schema:v1:* key namespace expected by search_schema_v1() and the WS
/ws/schema-progress endpoint.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

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


def index_named_db(db_name: str, *, force: bool = False) -> int:
    """
    Vectorise a production database configured in daibai.yaml.

    Returns the number of tables successfully indexed.
    """
    try:
        from daibai.core.config import load_config, get_config_file_path
        cfg = load_config()
        db_config = cfg.get_database(db_name)
        config_path = get_config_file_path()
    except ValueError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"  ERROR: Could not load daibai.yaml — {e}", file=sys.stderr)
        logger.warning("[index] %s: config load failed — %s", db_name, e)
        return 0

    config_src = str(config_path) if config_path else "(config path unknown)"
    logger.info(
        "[index] about to index db=%s because defined in daibai.yaml | "
        "config=%s",
        db_name, config_src,
    )
    logger.info("[index] %s: start (force=%s)", db_name, force)

    cache = _get_cache()
    if cache is None:
        logger.warning(
            "[index] %s: failed — no Redis cache (set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env)",
            db_name,
        )
        return 0

    sm = SchemaManager(
        config=db_config,
        cache_manager=cache,
    )

    n = sm.index_schema(
        schema_name=db_name,
        force=force,
        progress_cb=_make_progress_cb(),
    )
    if n > 0:
        logger.info("[index] %s: done — %d table(s)", db_name, n)
    else:
        logger.warning(
            "[index] %s: failed — 0 tables indexed (check Redis, embedding model, or DB connectivity)",
            db_name,
        )
    return n


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    pos_args = [a for a in args if not a.startswith("-") and a.strip()]
    force = "--force" in args or "-f" in args

    try:
        from daibai.core.config import load_config
        cfg = load_config()
        default_db = list(cfg.databases.keys())[0] if cfg.databases else None
    except Exception:
        default_db = None

    target = pos_args[0] if pos_args else default_db
    if not target:
        print(
            "  ERROR: No database target specified and daibai.yaml has no databases.\n"
            "         Usage: python scripts/index_db.py <db-name> [--force]\n"
            "         Add databases to daibai.yaml and try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    print()
    print("=" * 62)
    print(f"  DaiBai Semantic Schema Indexer")
    print(f"  Target  : {target}")
    print(f"  Force   : {'yes (bypassing refresh interval)' if force else 'no'}")
    print("=" * 62)
    print()
    print(f"  Source  : {target}  (from daibai.yaml)")
    print(f"  Redis   : schema:v1:* keys  (schema_name='{target}')")
    print()

    t0 = time.monotonic()

    count = index_named_db(target, force=force)

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
        print("     Run:  ./scripts/cli.sh verify-cache")
        print("   • Embedding model unavailable — check OPENAI_API_KEY (or")
        print("     whichever provider supplies embeddings in daibai.yaml)")
        print("   • Schema already up-to-date — re-run with --force to override")
        print()
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
