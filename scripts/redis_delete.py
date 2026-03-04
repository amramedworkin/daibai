#!/usr/bin/env python3
"""
Delete Redis keys: interactive selection or direct by key name.

Usage:
  ./scripts/cli.sh redis-delete                    # Interactive: list keys, select to delete
  ./scripts/cli.sh redis-delete "mykey"           # Delete specific key directly
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
        print("Error: No Redis connection. Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env", file=sys.stderr)
        return 1

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        # Direct delete: first arg is the key
        key = args[0]
        try:
            deleted = client.delete(key)
            client.close()
            if deleted:
                print(f"Deleted: {key}")
                return 0
            print(f"Key not found or already deleted: {key}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Interactive: list keys, let user select
    keys = []
    try:
        for key in client.scan_iter(match="*", count=1000):
            keys.append(key)
    finally:
        pass  # don't close yet

    keys.sort()
    if not keys:
        print("No keys in Redis.")
        client.close()
        return 0

    print("")
    print("Redis keys (select number to delete):")
    print("-" * 50)
    for i, key in enumerate(keys, 1):
        print(f"  {i:3}. {key}")
    print("-" * 50)
    print("Enter number to delete (or 0/Enter to cancel): ", end="", flush=True)

    try:
        line = sys.stdin.readline().strip()
    except (KeyboardInterrupt, EOFError):
        print("")
        client.close()
        return 0

    if not line:
        print("Cancelled.")
        client.close()
        return 0

    try:
        num = int(line)
    except ValueError:
        print("Invalid input. Cancelled.")
        client.close()
        return 1

    if num == 0:
        print("Cancelled.")
        client.close()
        return 0

    if num < 1 or num > len(keys):
        print("Invalid selection. Cancelled.")
        client.close()
        return 1

    key_to_delete = keys[num - 1]
    try:
        client.delete(key_to_delete)
        print(f"Deleted: {key_to_delete}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
