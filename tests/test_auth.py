"""Tests for API authentication."""

import pytest
from fastapi.testclient import TestClient

from daibai.api.server import app, get_current_user


def test_settings_returns_401_without_auth():
    """Calling /api/settings without Authorization header returns 401."""
    client = TestClient(app)
    response = client.get("/api/settings")
    assert response.status_code == 401


def test_settings_returns_200_with_valid_token():
    """Calling /api/settings with valid token (mocked) returns 200."""
    # Override get_current_user to simulate valid token
    def mock_get_current_user():
        return {"sub": "test-user", "email": "test@example.com"}

    app.dependency_overrides[get_current_user] = mock_get_current_user

    try:
        client = TestClient(app)
        # Any Bearer token works when we override the dependency
        response = client.get(
            "/api/settings",
            headers={"Authorization": "Bearer valid-test-token"},
        )
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
