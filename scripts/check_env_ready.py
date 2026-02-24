#!/usr/bin/env python3
"""
Environment readiness check for DaiBai.

Loads .env and runs daibai.core.env_ready. Runnable standalone or via cli.sh is-ready.
"""

import sys
from pathlib import Path

# Load .env before checking
_project_root = Path(__file__).resolve().parent.parent
for loc in [_project_root / ".env", Path.home() / ".daibai" / ".env"]:
    if loc.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(loc)
            break
        except ImportError:
            break

from daibai.core.env_ready import run

if __name__ == "__main__":
    strict = "--strict" in sys.argv
    sys.exit(run(verbose=True, strict=strict))
