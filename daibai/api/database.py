"""
Stateless conversation store backed by Azure Cosmos DB.

Uses azure.cosmos.aio.CosmosClient with DefaultAzureCredential. Async is critical
for web servers so one user's database save doesn't block others.
No local storage—all data lives in Cosmos DB.
"""

import os
from typing import Any, Dict, List, Optional

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential


class CosmosStore:
    """
    Stateless Cosmos DB store. Singleton client pattern: create once, reuse.
    Database: daibai-metadata, Container: conversations.
    Document: {"id": session_id, "messages": [...]}
    Partition key: /id
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        database_name: Optional[str] = None,
        container_name: Optional[str] = None,
    ):
        self._endpoint = (endpoint or os.environ.get("COSMOS_ENDPOINT", "")).strip().rstrip("/")
        self._database_name = database_name or os.environ.get("COSMOS_DATABASE", "daibai-metadata")
        self._container_name = container_name or os.environ.get("COSMOS_CONTAINER", "conversations")
        self._client: Optional[CosmosClient] = None
        self._credential: Optional[DefaultAzureCredential] = None

    @property
    def is_configured(self) -> bool:
        """True if COSMOS_ENDPOINT is set and store can connect."""
        return bool(self._endpoint)

    async def _ensure_client(self) -> CosmosClient:
        """Create and return the Cosmos client. Singleton: create once, reuse."""
        if self._client is not None:
            return self._client
        if not self._endpoint:
            raise RuntimeError(
                "COSMOS_ENDPOINT not set. Add to environment: "
                'export COSMOS_ENDPOINT="https://your-account.documents.azure.com:443/"'
            )
        self._credential = DefaultAzureCredential()
        self._client = CosmosClient(self._endpoint, credential=self._credential)
        return self._client

    async def close(self) -> None:
        """Close the client and credential."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    async def get_chat_history(self, session_id: str) -> list:
        """Fetch the document where id == session_id. Return messages list or [] if not found."""
        client = await self._ensure_client()
        database = client.get_database_client(self._database_name)
        container = database.get_container_client(self._container_name)
        try:
            doc = await container.read_item(item=session_id, partition_key=session_id)
            return doc.get("messages", [])
        except CosmosResourceNotFoundError:
            return []

    async def save_chat_history(self, session_id: str, messages: list) -> None:
        """Save chat history. Document structure: {"id": session_id, "messages": messages}."""
        client = await self._ensure_client()
        database = client.get_database_client(self._database_name)
        container = database.get_container_client(self._container_name)
        doc = {"id": session_id, "messages": messages}
        await container.upsert_item(doc)

    async def ping(self) -> bool:
        """Ping Cosmos DB to verify connectivity. Returns True if successful."""
        if not self._endpoint:
            return False
        try:
            client = await self._ensure_client()
            database = client.get_database_client(self._database_name)
            await database.read()
            return True
        except Exception:
            return False

    # Aliases for backward compatibility (server and tests use these)
    async def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Alias for get_chat_history."""
        return await self.get_chat_history(session_id)

    async def upsert_history(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Alias for save_chat_history."""
        await self.save_chat_history(session_id, messages)

    async def append_messages(
        self, session_id: str, new_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Fetch existing messages, append new ones, save, and return the full list."""
        messages = await self.get_chat_history(session_id)
        messages.extend(new_messages)
        await self.save_chat_history(session_id, messages)
        return messages

    async def list_conversations(self) -> List[Dict[str, Any]]:
        """
        List all conversations. Returns list of {id, title, created_at, message_count}.
        """
        client = await self._ensure_client()
        database = client.get_database_client(self._database_name)
        container = database.get_container_client(self._container_name)
        from datetime import datetime

        results: List[Dict[str, Any]] = []
        query = "SELECT c.id, c.messages FROM c"
        async for item in container.query_items(query=query):
            session_id = item.get("id", "")
            messages = item.get("messages", [])
            if not messages:
                title = "New conversation"
                created_at = datetime.now().isoformat()
            else:
                first_msg = messages[0]
                content = first_msg.get("content", "")
                title = content[:50] + "..." if len(content) > 50 else content or "New conversation"
                created_at = first_msg.get("timestamp", datetime.now().isoformat())
            results.append(
                {
                    "id": session_id,
                    "title": title,
                    "created_at": created_at,
                    "message_count": len(messages),
                }
            )
        return sorted(results, key=lambda x: x["created_at"], reverse=True)

    async def delete_conversation(self, session_id: str) -> None:
        """Delete a conversation document."""
        client = await self._ensure_client()
        database = client.get_database_client(self._database_name)
        container = database.get_container_client(self._container_name)
        try:
            await container.delete_item(item=session_id, partition_key=session_id)
        except CosmosResourceNotFoundError:
            pass  # Idempotent: already deleted


# Backward compatibility alias (server imports CosmosConversationStore)
CosmosConversationStore = CosmosStore
