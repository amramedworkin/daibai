#!/usr/bin/env python3
"""
Test Redis workflow: add dummy key, list, delete, list again.

Usage:
  ./scripts/cli.sh redis-test
"""

import subprocess
import sys
from pathlib import Path

# Bootstrap daibai onto path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

for loc in [_ROOT / ".env", Path.home() / ".daibai" / ".env"]:
    if loc.exists():
        load_dotenv(loc)
        break


def main() -> int:
    import redis
    from daibai.core.config import get_redis_connection_string

    conn_str = get_redis_connection_string()
    if not conn_str:
        print("Error: No Redis connection. Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env", file=sys.stderr)
        return 1

    if conn_str.lower().startswith("rediss://") and "ssl_cert_reqs" not in conn_str:
        sep = "&" if "?" in conn_str else "?"
        conn_str = f"{conn_str}{sep}ssl_cert_reqs=none"

    test_key = "_daibai_test_dummy_value"

    cli_sh = _ROOT / "scripts" / "cli.sh"
    if not cli_sh.exists():
        print("Error: scripts/cli.sh not found", file=sys.stderr)
        return 1

    try:
        client = redis.Redis.from_url(
            conn_str,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        client.ping()
    except Exception as e:
        print(f"Error: Cannot connect to Redis: {e}", file=sys.stderr)
        return 1

    def run(cmd: list) -> int:
        return subprocess.call(cmd, cwd=str(_ROOT))

    print("", flush=True)
    print("=== Redis Test: add → list → delete → list ===", flush=True)
    print("", flush=True)

    # Step 1: Add dummy key
    print("[1] Adding dummy key...", flush=True)
    try:
        client.set(test_key, "dummy test value for redis-test", ex=300)
        client.close()
        print(f"     Set: {test_key} = \"dummy test value for redis-test\" (TTL 300s)", flush=True)
    except Exception as e:
        print(f"     Error: {e}", file=sys.stderr)
        return 1

    print("", flush=True)
    # Step 2: List keys
    print("[2] Running redis-list (should show the added key):", flush=True)
    run(["/bin/bash", str(cli_sh), "redis-list"])

    print("", flush=True)
    # Step 3: Delete the key
    print("[3] Running redis-delete to remove the test key...", flush=True)
    run(["/bin/bash", str(cli_sh), "redis-delete", test_key])

    print("", flush=True)
    # Step 4: List again
    print("[4] Running redis-list again (key should be gone):", flush=True)
    run(["/bin/bash", str(cli_sh), "redis-list"])

    print("", flush=True)
    print("=== Redis test complete ===", flush=True)
    print("", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
