"""
Azure Cosmos DB cloud integration tests.

These tests talk to real Azure Cosmos DB using your credentials. They verify that
the connection, authentication, and basic create/read/delete operations work.
Run before deploying to ensure your Azure setup is correct.
"""

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("COSMOS_ENDPOINT", "").strip(),
        reason="COSMOS_ENDPOINT not set - cloud test requires live Cosmos DB",
    ),
]


@pytest.mark.cloud
async def test_cosmos_cloud_lifecycle():
    """
    Verifies we can create, read, and delete documents in real Azure Cosmos DB.
    Run this before deploying to confirm your Azure credentials and connection work.
    """
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    from azure.identity.aio import DefaultAzureCredential

    endpoint = os.environ["COSMOS_ENDPOINT"].strip().rstrip("/")
    database_name = os.environ.get("COSMOS_DATABASE", "daibai-metadata")
    container_name = os.environ.get("COSMOS_CONTAINER", "conversations")
    session_id = str(uuid.uuid4())

    doc = {
        "id": session_id,
        "messages": [
            {"role": "user", "content": "Cloud test message", "timestamp": "2024-01-01T12:00:00"},
            {"role": "assistant", "content": "SELECT 1", "sql": "SELECT 1"},
        ],
    }

    credential = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=credential)

    try:
        database = client.get_database_client(database_name)
        container = database.get_container_client(container_name)

        # 1. Create
        await container.upsert_item(doc)

        # 2. Read back and assert identical
        read_doc = await container.read_item(item=session_id, partition_key=session_id)
        assert read_doc["id"] == doc["id"]
        assert read_doc["messages"] == doc["messages"]

        # 3. Delete (cleanup)
        await container.delete_item(item=session_id, partition_key=session_id)

        # Verify deletion
        with pytest.raises(CosmosResourceNotFoundError):
            await container.read_item(item=session_id, partition_key=session_id)
    finally:
        await client.close()
        await credential.close()
