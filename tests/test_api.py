"""
API tests for the chat endpoint (/api/query).

These tests send real HTTP requests to the server but mock the LLM and use an
in-memory database. They verify that when you send a chat message, the server
correctly saves the conversation to the store.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from daibai.api.database import CosmosConversationStore
from daibai.api.server import app, get_store


class InMemoryStore(CosmosConversationStore):
    """In-memory store for testing - records all upserted data."""

    def __init__(self):
        self._data: dict = {}
        self._endpoint = "https://test.documents.azure.com:443/"  # Pretend configured

    async def get_history(self, session_id: str):
        return self._data.get(session_id, [])

    async def upsert_history(self, session_id: str, messages: list):
        self._data[session_id] = messages

    async def ping(self):
        return True


@pytest.fixture
def in_memory_store():
    """Store we can inspect after the request."""
    return InMemoryStore()


@pytest.fixture
def client(in_memory_store):
    """TestClient with in-memory store."""

    def _get_store_override(request: Request):
        return in_memory_store

    app.dependency_overrides[get_store] = _get_store_override

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_post_query_creates_record_in_store(client, in_memory_store):
    """
    When a user sends a chat message, the server saves both the user message and the AI response
    to the conversation store so the chat history persists.
    """
    with patch("daibai.api.server.get_agent") as mock_get_agent:
        agent = mock_get_agent.return_value
        agent.generate_sql_async = AsyncMock(return_value="SELECT 1")
        agent.run_sql = lambda _: None  # No DB for this test

        session_id = "test-session-123"
        response = client.post(
            "/api/query",
            json={"query": "count all users", "conversation_id": session_id, "execute": False},
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["conversation_id"] == session_id
        assert data["sql"] == "SELECT 1"

        # Verify record was created in store (in-memory _data populated by upsert_history)
        assert session_id in in_memory_store._data
        messages = in_memory_store._data[session_id]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "count all users"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "SELECT 1"


