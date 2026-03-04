#!/usr/bin/env python3
"""
Environment readiness check for DaiBai.

Loads .env, runs EnvValidator (hardened pre-flight), then component status report.
Covers every contingency: missing keys, placeholders, .env not found.
"""

import sys
from pathlib import Path

# Add project root to path so we can import daibai
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

from daibai.core.env_ready import EnvValidator, run

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def main() -> None:
    # Load environment variables first
    env_locations = [
        project_root / ".env",
        Path.home() / ".daibai" / ".env",
    ]
    loaded = False
    for env_path in env_locations:
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            loaded = True
            break

    # Intercept Tailgate
    if "--tailgate" in sys.argv:
        from daibai.core.env_ready import run_tailgate

        run_tailgate()
        sys.exit(0)

    if not loaded:
        print(f"{RED}[ERROR] .env file not found!{RESET}")
        print(f"  Looked in: {project_root / '.env'}, {Path.home() / '.daibai' / '.env'}")
        print("\nCreate .env from .env.example and add your credentials.")
        sys.exit(1)

    # Hardened validation
    is_valid, issues = EnvValidator.validate()
    if not is_valid:
        print(f"{RED}[ERROR] Environment validation failed!{RESET}")
        print(f"{YELLOW}The following keys are missing or contain placeholder values:{RESET}")
        for issue in issues:
            print(f"  - {RED}{issue}{RESET}")
        print("\nPlease update your .env file with real values before continuing.")
        sys.exit(1)

    # Success + component status report
    print(f"{GREEN}[SUCCESS] Environment is fully configured and ready!{RESET}\n")
    strict = "--strict" in sys.argv
    sys.exit(run(verbose=True, strict=strict))


if __name__ == "__main__":
    main()
