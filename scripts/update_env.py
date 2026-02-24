#!/usr/bin/env python3
"""
Smart Append: Idempotent .env updater. Prevents duplicate entries in config files.

Instead of "dumb" append (>>) which creates duplicate keys, Smart Append:
  1. Searches for the key in the file
  2. If found: Updates the value on that line (in-place replace)
  3. If not found: Appends KEY=VALUE at the end

Result: Exactly one entry per setting, no matter how many times setup runs.

Usage:
  python3 update_env.py <env_file> KEY VALUE
  python3 update_env.py <env_file> KEY1=value1 [KEY2=value2 ...]

Examples:
  python3 scripts/update_env.py .env REDIS_URL "rediss://:xxx@host:6380"
  python3 scripts/update_env.py .env REDIS_URL="$URL" REDIS_RESOURCE_GROUP="$RG"

Creates .env from .env.example if env_file does not exist.
"""

import sys
import tempfile
from pathlib import Path


def parse_updates(args: list[str]) -> dict[str, str]:
    """Parse KEY VALUE or KEY=value pairs from args."""
    updates = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if "=" in arg:
            idx = arg.index("=")
            key, value = arg[:idx], arg[idx + 1 :]
            updates[key] = value
            i += 1
        elif i + 1 < len(args):
            updates[arg] = args[i + 1]
            i += 2
        else:
            i += 1
    return updates


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write(
            "Usage: update_env.py <env_file> KEY VALUE\n"
            "   or: update_env.py <env_file> KEY1=value1 [KEY2=value2 ...]\n"
        )
        sys.exit(1)

    env_path = Path(sys.argv[1]).resolve()
    project_dir = env_path.parent
    example_path = project_dir / ".env.example"

    updates = parse_updates(sys.argv[2:])
    if not updates:
        sys.exit(0)

    # Ensure .env exists: create from example or empty
    if not env_path.exists():
        if example_path.exists():
            env_path.write_text(example_path.read_text())
        else:
            env_path.touch()

    lines = []
    written_keys = set()
    for line in env_path.read_text().splitlines(keepends=True):
        stripped = line.strip()
        matched = False
        for key in updates:
            if stripped.startswith(f"{key}="):
                matched = True
                if key not in written_keys:
                    lines.append(f"{key}={updates[key]}\n")
                    written_keys.add(key)
                break
        if not matched:
            lines.append(line)

    # Append any keys we didn't find
    for key, value in updates.items():
        if key not in written_keys:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append("\n# Updated by setup script\n")
            lines.append(f"{key}={value}\n")

    # Atomic write: write to temp file, then rename (idempotent, no partial writes)
    content = "".join(lines)
    fd, tmp_path = tempfile.mkstemp(dir=env_path.parent, prefix=".env.", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(env_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
