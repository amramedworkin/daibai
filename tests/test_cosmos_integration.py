"""
Azure Cosmos DB integration test - full lifecycle (Create, Read, Update, Delete).

Requires COSMOS_ENDPOINT and az login. Uses DefaultAzureCredential.
"""

import asyncio
import os
import sys
import uuid

import pytest

# Skip entire module if COSMOS_ENDPOINT not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("COSMOS_ENDPOINT", "").strip(),
    reason="COSMOS_ENDPOINT not set - integration test requires live Cosmos DB",
)


@pytest.mark.asyncio
async def test_cosmos_full_lifecycle():
    """
    Full lifecycle: Create document, Read & verify, Update (append), verify length, Delete.
    """
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    from azure.core.exceptions import AzureError
    from azure.identity.aio import DefaultAzureCredential

    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip().rstrip("/")
    database_name = os.environ.get("COSMOS_DATABASE", "daibai-metadata")
    container_name = os.environ.get("COSMOS_CONTAINER", "conversations")
    test_session_id = f"test-integration-{uuid.uuid4()}"

    credential = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=credential)

    try:
        database = client.get_database_client(database_name)
        container = database.get_container_client(container_name)

        # Create: Write test document
        doc = {
            "id": test_session_id,
            "messages": [{"role": "user", "content": "Testing Azure Flow"}],
        }
        await container.upsert_item(doc)

        # Read & Verify: Retrieve and assert content
        read_doc = await container.read_item(
            item=test_session_id, partition_key=test_session_id
        )
        messages = read_doc.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Testing Azure Flow"

        # Update: Append second message
        messages.append({"role": "assistant", "content": "Azure Flow verified"})
        updated_doc = {"id": test_session_id, "messages": messages}
        await container.upsert_item(updated_doc)

        # Verify list length is now 2
        read_doc2 = await container.read_item(
            item=test_session_id, partition_key=test_session_id
        )
        assert len(read_doc2.get("messages", [])) == 2

        # Cleanup: Delete test document
        await container.delete_item(
            item=test_session_id, partition_key=test_session_id
        )

        # Verify deletion
        with pytest.raises(CosmosResourceNotFoundError):
            await container.read_item(
                item=test_session_id, partition_key=test_session_id
            )

    except AzureError as e:
        print(f"Integration Test: FAILED - {e}")
        raise
    finally:
        await client.close()
        await credential.close()


def run_integration_test_standalone():
    """Run as script for CLI invocation. Prints PASSED/FAILED and exits with code."""
    if not os.environ.get("COSMOS_ENDPOINT", "").strip():
        print("Integration Test: FAILED - COSMOS_ENDPOINT not set", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(test_cosmos_full_lifecycle())
        print("Integration Test: PASSED")
    except Exception as e:
        print(f"Integration Test: FAILED - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_integration_test_standalone()
    sys.exit(0)
