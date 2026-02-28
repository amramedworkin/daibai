"""
CLI helper: list all users from the Cosmos DB Users container.

Usage (from project root, using venv Python):
    .venv/bin/python scripts/check_users.py [--format=json|table]

Outputs JSON on stdout so the caller (cli.sh) can parse it reliably.
Errors are written to stderr AND emitted as a sentinel JSON object
{"_error": "..."} on stdout so the shell can detect them with jq.

The script imports the real CosmosStore from daibai.api.database so
it always uses the same credentials and query logic as the backend.
"""

import asyncio
import json
import os
import sys


def _load_env() -> None:
    """Load .env from the project root (parent of scripts/) if present."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(project_root, ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


async def _fetch_users() -> list:
    """Import CosmosStore here (after env is loaded) and call list_users()."""
    # Add project root to sys.path so daibai package is importable.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from daibai.api.database import CosmosStore  # noqa: PLC0415

    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip().rstrip("/")
    database = os.environ.get("COSMOS_DATABASE", "daibai-metadata")

    if not endpoint:
        raise RuntimeError(
            "COSMOS_ENDPOINT is not set. "
            "Add it to .env or export it before running this script."
        )

    store = CosmosStore(
        endpoint=endpoint,
        database_name=database,
        container_name="conversations",  # list_users() always targets 'Users'
    )
    try:
        return await store.list_users()
    finally:
        await store.close()


def main() -> None:
    _load_env()

    use_table = "--format=table" in sys.argv or "-t" in sys.argv

    try:
        users = asyncio.run(_fetch_users())
    except Exception as exc:
        print(json.dumps({"_error": str(exc)}))
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if use_table:
        if not users:
            print("No users found.")
            return
        print(f"Found {len(users)} user(s):\n")
        print(f"  {'UID (Firebase)':<36}  {'Email':<34}  {'Registered':<26}  Display Name")
        print(f"  {'-'*36}  {'-'*34}  {'-'*26}  {'-'*20}")
        for u in users:
            uid = u.get("uid") or u.get("id") or "—"
            email = u.get("email") or u.get("username") or "—"
            ts = u.get("onboarded_at") or u.get("created_at") or "—"
            name = u.get("display_name") or "—"
            print(f"  {uid:<36}  {email:<34}  {ts:<26}  {name}")
    else:
        print(json.dumps(users))


if __name__ == "__main__":
    main()
