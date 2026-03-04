#!/usr/bin/env python3
"""
List all Redis keys with their value size and TTL.

Usage:
  python scripts/redis_list_keys.py [--pattern PATTERN]
  ./scripts/cli.sh redis-list

Output: Table with key name, value size (bytes), and TTL (seconds or "no expiry").
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


def _format_size(n: int | None) -> str:
    """Format bytes for display."""
    if n is None:
        return "-"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _format_ttl(ttl: int) -> str:
    """Format TTL for display."""
    if ttl == -1:
        return "no expiry"
    if ttl == -2:
        return "(expired)"
    if ttl < 60:
        return f"{ttl}s"
    if ttl < 3600:
        return f"{ttl // 60}m"
    if ttl < 86400:
        return f"{ttl // 3600}h"
    return f"{ttl // 86400}d"


def main() -> int:
    import redis
    from daibai.core.config import get_redis_connection_string

    conn_str = get_redis_connection_string()
    if not conn_str:
        print("Error: No Redis connection. Set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env", file=sys.stderr)
        return 1

    pattern = "*"
    args = sys.argv[1:]
    if args and args[0] == "--pattern" and len(args) >= 2:
        pattern = args[1]
    elif args and not args[0].startswith("-"):
        pattern = args[0]

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
    except Exception as e:
        print(f"Error: Cannot connect to Redis: {e}", file=sys.stderr)
        return 1

    rows = []
    try:
        for key in client.scan_iter(match=pattern, count=1000):
            try:
                ttl = client.ttl(key)
                size = None
                try:
                    size = client.memory_usage(key)
                except (redis.ResponseError, redis.DataError):
                    pass
                if size is None:
                    ktype = client.type(key)
                    if ktype == "string":
                        size = client.strlen(key)
                rows.append((key, size, ttl))
            except (redis.ResponseError, redis.DataError):
                rows.append((key, None, -2))
    finally:
        client.close()

    rows.sort(key=lambda r: r[0])

    if not rows:
        print(f"No keys matching pattern: {pattern}")
        return 0

    # Column widths
    max_key = min(max(len(r[0]) for r in rows), 80)
    max_size = 12
    max_ttl = 12

    print("")
    print("Redis keys (pattern: {})".format(pattern))
    print("=" * (max_key + max_size + max_ttl + 10))
    print(f"{'KEY':<{max_key}} | {'SIZE':>{max_size}} | {'TTL':<{max_ttl}}")
    print("-" * (max_key + max_size + max_ttl + 10))

    for key, size, ttl in rows:
        key_disp = key if len(key) <= max_key else key[: max_key - 3] + "..."
        size_str = _format_size(size)
        ttl_str = _format_ttl(ttl)
        print(f"{key_disp:<{max_key}} | {size_str:>{max_size}} | {ttl_str:<{max_ttl}}")

    print("=" * (max_key + max_size + max_ttl + 10))
    print(f"Total: {len(rows)} key(s)")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
