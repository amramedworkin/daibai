#!/usr/bin/env python3
"""
Phase 4 Step 1: Verify secretless Azure authentication.

Attempts to list Cosmos DB containers using DefaultAzureCredential (no password).
If successful, the "Azureification" is working—your app can run without COSMOS_KEY.
"""

import os
import sys
from pathlib import Path

# Load .env from project
for loc in [Path.cwd() / ".env", Path.home() / ".daibai" / ".env"]:
    if loc.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(loc)
            break
        except ImportError:
            break

try:
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    from azure.core.exceptions import AzureError
except ImportError as e:
    print("ERROR: Missing dependencies.", file=sys.stderr)
    print("  pip install azure-cosmos azure-identity", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip().rstrip("/")
    if not endpoint:
        print("ERROR: COSMOS_ENDPOINT not set.", file=sys.stderr)
        print("  export COSMOS_ENDPOINT='https://your-account.documents.azure.com:443/'", file=sys.stderr)
        return 1

    try:
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        database_name = os.environ.get("COSMOS_DATABASE", "daibai-metadata")
        database = client.get_database_client(database_name)
        containers = list(database.list_containers())
        print(f"OK: Listed {len(containers)} container(s) in {database_name}")
        for c in containers[:5]:
            print(f"  - {c['id']}")
        if len(containers) > 5:
            print(f"  ... and {len(containers) - 5} more")
        print("")
        print("Azureification verified: Cosmos DB accessible without COSMOS_KEY.")
        return 0
    except AzureError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        print("  Ensure: az login, cosmos-role assignment, COSMOS_ENDPOINT correct.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
