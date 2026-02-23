"""
Production-grade integration tests for CosmosStore against live Azure Cosmos DB.

Validates CosmosStore (database.py) with DefaultAzureCredential, singleton client
behavior, async methods, E2E lifecycle, and graceful shutdown.
Requires COSMOS_ENDPOINT and az login.
"""

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.cloud,
    pytest.mark.skipif(
        not os.environ.get("COSMOS_ENDPOINT", "").strip(),
        reason="COSMOS_ENDPOINT not set - cloud test requires live Cosmos DB",
    ),
]


@pytest.fixture
def test_session_id():
    """Unique session ID for each test run."""
    return f"test-cosmos-store-{uuid.uuid4()}"


@pytest.fixture
def complex_messages():
    """Complex message list mimicking real chat history."""
    return [
        {
            "role": "user",
            "content": "Show me all users with active subscriptions",
            "timestamp": "2024-01-15T10:30:00",
        },
        {
            "role": "assistant",
            "content": "SELECT * FROM users WHERE subscription_status = 'active'",
            "sql": "SELECT * FROM users WHERE subscription_status = 'active'",
            "results": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            "timestamp": "2024-01-15T10:30:01",
        },
        {
            "role": "user",
            "content": "Filter by region US only",
            "timestamp": "2024-01-15T10:31:00",
        },
        {
            "role": "assistant",
            "content": "SELECT * FROM users WHERE subscription_status = 'active' AND region = 'US'",
            "sql": "SELECT * FROM users WHERE subscription_status = 'active' AND region = 'US'",
            "timestamp": "2024-01-15T10:31:02",
        },
    ]


async def test_cosmos_store_singleton_client(test_session_id, complex_messages):
    """
    Verify that multiple operations use the same client instance (connection pool shared).
    """
    from daibai.api.database import CosmosStore

    store = CosmosStore()
    assert store._client is None

    # First operation creates client
    await store.save_chat_history(test_session_id, complex_messages)
    client_after_save = store._client
    assert client_after_save is not None

    # Second operation reuses same client
    retrieved = await store.get_chat_history(test_session_id)
    client_after_get = store._client
    assert client_after_get is client_after_save
    assert id(client_after_get) == id(client_after_save)

    # Cleanup
    await store.delete_conversation(test_session_id)
    await store.close()


async def test_cosmos_store_e2e_lifecycle(test_session_id, complex_messages):
    """
    E2E: save_chat_history → get_chat_history → assert exact match → cleanup.
    Uses DefaultAzureCredential (production auth flow).
    """
    from daibai.api.database import CosmosStore

    store = CosmosStore()

    try:
        # Save
        await store.save_chat_history(test_session_id, complex_messages)

        # Retrieve and assert exact match
        retrieved = await store.get_chat_history(test_session_id)
        assert retrieved == complex_messages
        assert len(retrieved) == 4
        assert retrieved[0]["role"] == "user"
        assert retrieved[0]["content"] == "Show me all users with active subscriptions"
        assert retrieved[1]["sql"] == "SELECT * FROM users WHERE subscription_status = 'active'"
        assert retrieved[3]["role"] == "assistant"

        # Cleanup
        await store.delete_conversation(test_session_id)

        # Verify document is gone
        after_delete = await store.get_chat_history(test_session_id)
        assert after_delete == []
    finally:
        await store.close()


async def test_cosmos_store_fastapi_lifespan_simulation(test_session_id):
    """
    Simulate FastAPI lifespan: init store → perform operation → close (shutdown).
    Verifies graceful cleanup without errors.
    """
    from daibai.api.database import CosmosStore

    store = CosmosStore()
    messages = [{"role": "user", "content": "Lifespan test", "timestamp": "2024-01-01T12:00:00"}]

    try:
        # Simulate startup: store created
        await store.save_chat_history(test_session_id, messages)
        retrieved = await store.get_chat_history(test_session_id)
        assert retrieved == messages

        # Simulate shutdown: explicit close
        await store.close()

        # Verify client and credential are cleaned up
        assert store._client is None
        assert store._credential is None

        # Cleanup test document (need raw client since we closed)
        from azure.cosmos.aio import CosmosClient
        from azure.identity.aio import DefaultAzureCredential

        endpoint = os.environ["COSMOS_ENDPOINT"].strip().rstrip("/")
        db_name = os.environ.get("COSMOS_DATABASE", "daibai-metadata")
        container_name = os.environ.get("COSMOS_CONTAINER", "conversations")
        cred = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=cred)
        try:
            container = client.get_database_client(db_name).get_container_client(container_name)
            await container.delete_item(item=test_session_id, partition_key=test_session_id)
        except Exception:
            pass
        finally:
            await client.close()
            await cred.close()
    except Exception:
        await store.close()
        raise
