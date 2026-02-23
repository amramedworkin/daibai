"""
Unit tests for the conversation database layer (CosmosConversationStore).

These tests mock Cosmos DB and run entirely offline. They verify that we correctly
read, write, and transform conversation data—useful when Azure is down or you're offline.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_container():
    """Mock ContainerProxy with async methods."""
    container = MagicMock()
    container.read_item = AsyncMock()
    container.upsert_item = AsyncMock()
    container.delete_item = AsyncMock()
    container.query_items = MagicMock()
    return container


@pytest.fixture
def mock_database(mock_container):
    """Mock DatabaseProxy that returns our mock container."""
    database = MagicMock()
    database.get_container_client.return_value = mock_container
    database.read = AsyncMock()
    return database


@pytest.fixture
def mock_client(mock_database):
    """Mock CosmosClient that returns our mock database."""
    client = MagicMock()
    client.get_database_client.return_value = mock_database
    return client


async def _make_async_iter(items):
    """Turn a list into an async iterator."""
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_get_history_returns_messages_from_doc(mock_container, mock_client):
    """When a conversation document exists in Cosmos, fetching it returns the full list of chat messages."""
    from daibai.api.database import CosmosConversationStore

    mock_container.read_item.return_value = {
        "id": "session-123",
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ],
    }

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    messages = await store.get_history("session-123")

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[1]["role"] == "assistant"
    mock_container.read_item.assert_called_once_with(
        item="session-123", partition_key="session-123"
    )


@pytest.mark.asyncio
async def test_get_history_returns_empty_on_cosmos_not_found(mock_container, mock_client):
    """When a conversation does not exist yet, fetching it returns an empty list instead of raising an error."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    from daibai.api.database import CosmosConversationStore

    mock_container.read_item.side_effect = CosmosResourceNotFoundError()

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    messages = await store.get_history("session-123")

    assert messages == []


@pytest.mark.asyncio
async def test_get_history_returns_empty_when_messages_key_missing(mock_container, mock_client):
    """When a document exists but has no messages key, we safely return an empty list."""
    from daibai.api.database import CosmosConversationStore

    mock_container.read_item.return_value = {"id": "session-123"}

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    messages = await store.get_history("session-123")

    assert messages == []


@pytest.mark.asyncio
async def test_upsert_history_calls_container_with_correct_format(mock_container, mock_client):
    """Saving a conversation writes the session id and message list to Cosmos in the correct format."""
    from daibai.api.database import CosmosConversationStore

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    messages = [
        {"role": "user", "content": "Query", "timestamp": "2024-01-01T12:00:00"},
        {"role": "assistant", "content": "SELECT 1", "sql": "SELECT 1"},
    ]
    await store.upsert_history("session-789", messages)

    mock_container.upsert_item.assert_called_once()
    doc = mock_container.upsert_item.call_args[0][0]
    assert doc["id"] == "session-789"
    assert doc["messages"] == messages
    # Partition key is /id, so id field serves as partition key
    assert "id" in doc


@pytest.mark.asyncio
async def test_append_messages_fetches_extends_upserts(mock_container, mock_client):
    """Appending new messages loads the existing conversation, adds the new messages, saves everything, and returns the full list."""
    from daibai.api.database import CosmosConversationStore

    mock_container.read_item.return_value = {
        "id": "session-456",
        "messages": [{"role": "user", "content": "First"}],
    }

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    new_messages = [{"role": "assistant", "content": "Reply"}]
    result = await store.append_messages("session-456", new_messages)

    assert len(result) == 2
    assert result[0]["content"] == "First"
    assert result[1]["content"] == "Reply"

    mock_container.upsert_item.assert_called_once()
    upserted = mock_container.upsert_item.call_args[0][0]
    assert upserted["id"] == "session-456"
    assert len(upserted["messages"]) == 2


@pytest.mark.asyncio
async def test_list_conversations_transforms_to_summary_format(mock_container, mock_client):
    """Listing conversations converts raw Cosmos documents into a summary with id, title, date, and message count."""
    from daibai.api.database import CosmosConversationStore

    mock_container.query_items.return_value = _make_async_iter([
        {
            "id": "conv-1",
            "messages": [
                {"role": "user", "content": "Short", "timestamp": "2024-01-01T12:00:00"},
            ],
        },
        {
            "id": "conv-2",
            "messages": [
                {"role": "user", "content": "A" * 60, "timestamp": "2024-01-02T12:00:00"},
            ],
        },
        {"id": "conv-3", "messages": []},
    ])

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    results = await store.list_conversations()

    assert len(results) == 3
    assert results[0]["id"] == "conv-3"
    assert results[0]["title"] == "New conversation"
    assert results[0]["message_count"] == 0

    assert results[1]["id"] == "conv-2"
    assert "..." in results[1]["title"]
    assert results[1]["message_count"] == 1

    assert results[2]["id"] == "conv-1"
    assert results[2]["title"] == "Short"
    assert results[2]["created_at"] == "2024-01-01T12:00:00"


@pytest.mark.asyncio
async def test_delete_conversation_idempotent(mock_container, mock_client):
    """Deleting a conversation that does not exist does not raise an error (safe to delete twice)."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    from daibai.api.database import CosmosConversationStore

    mock_container.delete_item.side_effect = CosmosResourceNotFoundError()

    store = CosmosConversationStore(endpoint="https://test.documents.azure.com:443/")
    store._client = mock_client
    store._credential = MagicMock()

    await store.delete_conversation("nonexistent")
    mock_container.delete_item.assert_called_once_with(
        item="nonexistent", partition_key="nonexistent"
    )
