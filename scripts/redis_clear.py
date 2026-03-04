#!/usr/bin/env python3
"""
Clear all keys from the current Redis database (FLUSHDB).

Usage:
  ./scripts/cli.sh redis-clear           # Prompts for confirmation
  ./scripts/cli.sh redis-clear --force    # Skip confirmation
"""

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


def _get_client():
    """Connect to Redis. Returns (client, conn_str) or (None, None)."""
    import redis
    from daibai.core.config import get_redis_connection_string

    conn_str = get_redis_connection_string()
    if not conn_str:
        return None, None

    if conn_str.lower().startswith("rediss://") and "ssl_cert_reqs" not in conn_str:
        sep = "&" if "?" in conn_str else "?"
        conn_str = f"{conn_str}{sep}ssl_cert_reqs=none"

    try:
        client = redis.Redis.from_url(
            conn_str,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        client.ping()
        return client, conn_str
    except Exception:
        return None, None


def main() -> int:
    client, _ = _get_client()
    if not client:
        print(
            "Error: No Redis connection. Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env",
            file=sys.stderr,
        )
        return 1

    force = "--force" in sys.argv or "-f" in sys.argv

    try:
        dbsize = client.dbsize()
    except Exception:
        dbsize = 0

    if not force:
        print("")
        print("WARNING: This will DELETE ALL keys in the current Redis database.")
        print(f"         (~{dbsize} key(s) will be removed)")
        print("")
        print("Enter 'yes' to confirm: ", end="", flush=True)
        try:
            line = sys.stdin.readline().strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("")
            client.close()
            return 0
        if line != "yes":
            print("Cancelled.")
            client.close()
            return 0

    try:
        client.flushdb()
        print(f"Redis cleared. Removed all keys from current database.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
