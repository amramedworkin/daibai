#!/usr/bin/env python3
"""
Clean .env: Remove duplicate keys and malformed entries.

Run this to fix a .env file that was corrupted by repeated "dumb" appends (>>).
Keeps the last occurrence of each key (last wins). Preserves comments and empty lines.
Removes lines that don't match KEY=value format.

Usage:
  python3 scripts/clean_env.py [path/to/.env]
  ./scripts/cli.sh env-clean [path]

Defaults to project .env if no path given.
"""

import re
import sys
from pathlib import Path

# Valid KEY=value: key is identifier, value can be anything including empty
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", re.DOTALL)


def parse_env_content(text):
    """
    Parse .env content. Returns (other_lines, key_to_line_value, malformed).
    key_to_line_value: key -> (line_num, value) for last occurrence
    malformed: list of (line_num, line) for invalid entries
    """
    key_seen = {}
    malformed = []
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_VALUE_RE.match(stripped)
        if m:
            key, value = m.group(1), m.group(2).rstrip()
            key_seen[key] = (i, value)
        else:
            malformed.append((i, line.rstrip("\n")))

    return key_seen, malformed


def clean_env(env_path, in_place=True):
    """
    Clean env file: deduplicate keys (last wins), remove malformed lines.
    Returns (changed, messages).
    """
    env_path = Path(env_path)
    if not env_path.exists():
        return False, [f"File not found: {env_path}"]

    text = env_path.read_text()
    key_to_val, malformed = parse_env_content(text)

    messages = []
    if malformed:
        messages.append(f"Removed {len(malformed)} malformed line(s): {[m[0] for m in malformed]}")

    # Build output: preserve order of first occurrence, use last value
    seen = set()
    out_lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        m = _KEY_VALUE_RE.match(stripped)
        if m:
            key = m.group(1)
            if key in seen:
                continue  # skip duplicate
            seen.add(key)
            _, value = key_to_val[key]
            out_lines.append(f"{key}={value}\n")

    new_text = "".join(out_lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"

    changed = new_text != text
    if changed and in_place:
        env_path.write_text(new_text)
        messages.append(f"Cleaned {env_path} (duplicates removed, malformed removed)")

    return changed, messages


def main():
    project_root = Path(__file__).resolve().parent.parent
    env_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else project_root / ".env"

    changed, messages = clean_env(env_path)
    for m in messages:
        print(m)
    sys.exit(0 if changed or not messages else 1)


if __name__ == "__main__":
    main()
