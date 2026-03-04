#!/usr/bin/env python3
"""
DaiBai System Reset
==================
Clears all cached and persisted state for a clean-slate testing environment.

Resets:
  1. Redis cache (semantic cache, L1 cache keys)
  2. Semantic schema indexes (schema:v1:*, schema:status:*)
  3. Firebase Authentication (all users)
  4. Cosmos DB (Users + Conversations)
  5. Log files (rotate/archive)
  6. Local state (~/.daibai: memory, exports, uploads, preferences)

Usage:
  python scripts/system_reset.py [--force] [--skip-firebase-cosmos] [--skip-local]
  ./scripts/cli.sh system-reset [--force]
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Bootstrap daibai onto path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env() -> None:
    """Load .env so Redis, Cosmos, Firebase config are available."""
    from dotenv import load_dotenv
    for loc in [
        _ROOT / ".env",
        Path.home() / ".daibai" / ".env",
    ]:
        if loc.exists():
            load_dotenv(loc)
            break


def _clear_redis() -> tuple[bool, str]:
    """Clear Redis: semantic cache, schema indexes, schema status."""
    from daibai.core.config import get_redis_connection_string
    conn_str = get_redis_connection_string()
    if not conn_str:
        return False, "Redis not configured (REDIS_URL / AZURE_REDIS_CONNECTION_STRING)"
    try:
        import redis
        # Azure rediss:// needs ssl_cert_reqs=none
        if conn_str.lower().startswith("rediss://") and "ssl_cert_reqs" not in conn_str:
            sep = "&" if "?" in conn_str else "?"
            conn_str = f"{conn_str}{sep}ssl_cert_reqs=none"
        client = redis.Redis.from_url(conn_str, decode_responses=True, socket_connect_timeout=5)
        client.ping()

        patterns = [
            "schema:v1:*",
            "schema:status:*",
            "daibai:semantic_cache:*",
            "semantic:*",
        ]
        total = 0
        for pattern in patterns:
            keys = client.keys(pattern)
            if keys:
                client.delete(*keys)
                total += len(keys)
        client.close()
        return True, f"Cleared {total} Redis key(s)"
    except Exception as e:
        return False, str(e)


def _init_firebase() -> bool:
    """Initialize Firebase Admin SDK. Return True if successful."""
    cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if cred_path and not os.path.isabs(cred_path):
        cred_path = str(_ROOT / cred_path)
    if not cred_path or not Path(cred_path).exists():
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
        try:
            firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        return True
    except ImportError:
        return False


def _wipe_firebase_and_cosmos_async() -> tuple[bool | None, str]:
    """Delete all Firebase users and wipe Cosmos Users + Conversations.
    Returns (None, msg) when skipped (not configured), (True, msg) on success, (False, msg) on error.
    """
    import asyncio
    if not _init_firebase():
        return None, "Firebase not configured (FIREBASE_SERVICE_ACCOUNT_JSON) — skipped"

    import firebase_admin.auth as fb_auth
    from daibai.api.database import CosmosStore

    async def _run() -> tuple[int, int, int]:
        fb_uids = []
        page = fb_auth.list_users()
        while page:
            fb_uids.extend(u.uid for u in page.users)
            page = page.get_next_page()
        fb_count = 0
        for i in range(0, len(fb_uids), 100):
            result = fb_auth.delete_users(fb_uids[i:i + 100])
            fb_count += result.success_count
            if result.failure_count:
                for err in result.errors:
                    print(f"  WARN: Firebase delete uid={err.uid}: {err.reason}", file=sys.stderr)
        store = CosmosStore()
        users_del = 0
        convs_del = 0
        if store.is_configured:
            try:
                users_del = await store.delete_all_users()
                convs_del = await store.delete_all_conversations()
            finally:
                await store.close()
        return fb_count, users_del, convs_del

    try:
        fb_count, users_del, convs_del = asyncio.run(_run())
        return True, f"Firebase: {fb_count}; Cosmos: {users_del} users, {convs_del} conversations"
    except Exception as e:
        return False, str(e)


def _rotate_logs() -> tuple[bool, str]:
    """Archive/remove log files so next server start writes fresh logs."""
    log_dir = _ROOT / "logs"
    if not log_dir.exists():
        return True, "No logs directory"
    count = 0
    for f in log_dir.glob("daibai.log*"):
        try:
            f.unlink()
            count += 1
        except OSError as e:
            return False, f"Failed to remove {f}: {e}"
    return True, f"Removed {count} log file(s)"


def _clear_local_state() -> tuple[bool, str]:
    """Clear ~/.daibai memory, exports, uploads, preferences."""
    home = Path.home()
    base = home / ".daibai"
    if not base.exists():
        return True, "No ~/.daibai directory"
    dirs_to_clear = ["memory", "exports", "uploads"]
    files_to_remove = ["preferences.json"]
    cleared = []
    for d in dirs_to_clear:
        p = base / d
        if p.exists() and p.is_dir():
            try:
                shutil.rmtree(p)
                cleared.append(d)
            except OSError as e:
                return False, f"Failed to remove {p}: {e}"
    for f in files_to_remove:
        p = base / f
        if p.exists():
            try:
                p.unlink()
                cleared.append(f)
            except OSError as e:
                return False, f"Failed to remove {p}: {e}"
    return True, f"Cleared: {', '.join(cleared) or 'nothing'}"


def _reset_playground_db() -> tuple[bool, str]:
    """Reset playground.db from chinook_master.db (sandbox)."""
    data_dir = _ROOT / "data"
    play_db = data_dir / "playground.db"
    master_db = data_dir / "chinook_master.db"
    if not master_db.exists():
        return False, "chinook_master.db not found"
    try:
        if play_db.exists():
            play_db.unlink()
        shutil.copy2(master_db, play_db)
        return True, "playground.db reset from chinook_master.db"
    except OSError as e:
        return False, str(e)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(
        prog="system_reset",
        description="DaiBai system reset — clear all cached and persisted state for testing.",
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        help="Skip confirmation prompt",
    )
    p.add_argument(
        "--skip-firebase-cosmos",
        action="store_true",
        help="Skip Firebase + Cosmos wipe (e.g. when not configured)",
    )
    p.add_argument(
        "--skip-local",
        action="store_true",
        help="Skip clearing ~/.daibai (memory, exports, uploads, preferences)",
    )
    p.add_argument(
        "--playground",
        action="store_true",
        help="Also reset playground.db from chinook_master.db",
    )
    args = p.parse_args()

    _load_env()

    print()
    print("=" * 62)
    print("  DaiBai System Reset")
    print("=" * 62)
    print()
    print("  This will:")
    print("    1. Clear Redis cache & semantic indexes")
    if not args.skip_firebase_cosmos:
        print("    2. Delete ALL Firebase Authentication users")
        print("    3. Delete ALL Cosmos DB Users + Conversations")
    else:
        print("    2–3. (Skip Firebase + Cosmos)")
    print("    4. Rotate/clear log files")
    if not args.skip_local:
        print("    5. Clear ~/.daibai (memory, exports, uploads, preferences)")
    else:
        print("    5. (Skip local state)")
    if args.playground:
        print("    6. Reset playground.db from chinook_master.db")
    print()
    print("  This action CANNOT be undone.")
    print()

    if not args.force:
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("  Cancelled.")
            sys.exit(0)

    results = []

    # 1. Redis
    ok, msg = _clear_redis()
    results.append(("Redis", ok, msg))

    # 2–3. Firebase + Cosmos
    if not args.skip_firebase_cosmos:
        ok, msg = _wipe_firebase_and_cosmos_async()
        results.append(("Firebase + Cosmos", ok, msg))
    else:
        results.append(("Firebase + Cosmos", None, "skipped"))

    # 4. Logs
    ok, msg = _rotate_logs()
    results.append(("Logs", ok, msg))

    # 5. Local state
    if not args.skip_local:
        ok, msg = _clear_local_state()
        results.append(("Local ~/.daibai", ok, msg))
    else:
        results.append(("Local ~/.daibai", None, "skipped"))

    # 6. Playground (optional)
    if args.playground:
        ok, msg = _reset_playground_db()
        results.append(("Playground DB", ok, msg))

    # Report
    print()
    for name, ok, msg in results:
        if ok is True:
            print(f"  [OK]   {name}: {msg}")
        elif ok is False:
            print(f"  [FAIL] {name}: {msg}")
        else:
            print(f"  [skip] {name}: {msg}")
    print()
    print("  System reset complete.")
    print()


if __name__ == "__main__":
    main()
