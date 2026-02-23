#!/usr/bin/env python3
"""
Cosmos DB validation script - "Golden Ticket" health check.

Tests Read/Write/Delete operations on Azure Cosmos DB using DefaultAzureCredential.
Validates: authentication (az login), permission (role assignment), plumbing (COSMOS_ENDPOINT).
"""

import os
import sys

try:
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    from azure.core.exceptions import AzureError
except ImportError as e:
    print("ERROR: Missing dependencies.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Using project venv:", file=sys.stderr)
    print("    source .venv/bin/activate", file=sys.stderr)
    print("    pip install -e .", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Or install directly:", file=sys.stderr)
    print("    pip install azure-cosmos azure-identity", file=sys.stderr)
    sys.exit(1)

# ANSI colors (disabled when not a TTY)
def _color():
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

RED = "\033[0;31m" if _color() else ""
GREEN = "\033[0;32m" if _color() else ""
YELLOW = "\033[1;33m" if _color() else ""
CYAN = "\033[0;36m" if _color() else ""
BOLD = "\033[1m" if _color() else ""
DIM = "\033[2m" if _color() else ""
NC = "\033[0m" if _color() else ""


def main():
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    if not endpoint or not endpoint.strip():
        print(f"{RED}ERROR: COSMOS_ENDPOINT not set.{NC}", file=sys.stderr)
        print("Add to .bashrc:", file=sys.stderr)
        print('  export COSMOS_ENDPOINT="https://daibai-metadata.documents.azure.com:443/"', file=sys.stderr)
        sys.exit(1)

    endpoint = endpoint.strip().rstrip("/")
    database_name = os.environ.get("COSMOS_DATABASE", "daibai-metadata")
    container_name = os.environ.get("COSMOS_CONTAINER", "conversations")
    test_id = "test-session"

    try:
        print(f"{DIM}Connecting to Cosmos DB...{NC}")
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential)

        database = client.get_database_client(database_name)
        container = database.get_container_client(container_name)

        # Create: Upsert test document
        print(f"{DIM}Create: Upserting test document...{NC}")
        test_doc = {"id": test_id, "message": "Hello Azure"}
        container.upsert_item(test_doc)
        print(f"  {GREEN}✓ OK{NC}")

        # Read: Retrieve and print
        print(f"{DIM}Read: Retrieving document...{NC}")
        read_doc = container.read_item(item=test_id, partition_key=test_id)
        print(f"  {DIM}Document: {read_doc}{NC}")

        # Delete: Remove test document
        print(f"{DIM}Delete: Removing test document...{NC}")
        container.delete_item(item=test_id, partition_key=test_id)
        print(f"  {GREEN}✓ OK{NC}")

        print("")
        print(f"{GREEN}{BOLD}✓ GOLDEN TICKET VALID{NC}")
        print(f"{GREEN}  Cosmos DB Read/Write/Delete validation passed.{NC}")
        print("")
        print(f"  {DIM}Verified:{NC}")
        print(f"  {GREEN}  ✓{NC} Authentication (az login via DefaultAzureCredential)")
        print(f"  {GREEN}  ✓{NC} Permission (Data Contributor role active)")
        print(f"  {GREEN}  ✓{NC} Plumbing (COSMOS_ENDPOINT set correctly)")
        print("")
        return 0

    except AzureError as e:
        print("", file=sys.stderr)
        print(f"{RED}✗ FAILED: Azure operation failed{NC}", file=sys.stderr)
        print(f"{RED}  {e}{NC}", file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("", file=sys.stderr)
        print(f"{RED}✗ FAILED: {e}{NC}", file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
