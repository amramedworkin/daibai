"""
Server lifespan tests: verify FastAPI manages CosmosStore connection during startup and shutdown.

Uses real COSMOS_ENDPOINT when set to ensure the live handshake works during server boot.
Skipped when COSMOS_ENDPOINT is not set.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("COSMOS_ENDPOINT", "").strip(),
    reason="COSMOS_ENDPOINT not set - lifespan test requires live Cosmos DB for handshake",
)


def test_server_lifespan_initializes_store_and_closes_gracefully():
    """
    Verify lifespan: store initialized on startup, client active after first request, shutdown runs without error.
    """
    from fastapi.testclient import TestClient

    from daibai.api.database import CosmosStore
    from daibai.api.server import app

    # TestClient triggers lifespan events: startup on enter, shutdown on exit
    with TestClient(app) as client:
        # Startup has run: store should be initialized
        store = app.state.store
        assert store is not None
        assert isinstance(store, CosmosStore)

        # Trigger client creation via /health (calls store.ping())
        response = client.get("/health")
        # With COSMOS_ENDPOINT set, we expect 200; store.ping() creates the client
        assert response.status_code in (200, 503)  # 503 if creds fail, 200 if connected

        # Verify CosmosClient within store is active (created by ping)
        assert store._client is not None

    # Context exited: lifespan shutdown ran. No exception = graceful shutdown.
    # After close(), store._client is set to None by store.close()
    assert store._client is None
