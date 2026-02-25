"""
Mock test: Token from a non-DaiBai tenant returns 403 Forbidden.

Patches the JWK client and jwt.decode so we inject a payload with tid from a wrong tenant.
The real tid check in auth.validate_token should reject it.
"""

from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from daibai.api.server import app


def test_token_from_wrong_tenant_returns_403():
    """
    A token that passes signature verification but has tid from a different tenant
    must be rejected with 403 Forbidden.
    """
    wrong_tid = "wrong-tenant-id-12345"
    mock_payload = {"tid": wrong_tid, "sub": "user-xyz", "aud": "some-client"}

    mock_signing_key = MagicMock()
    with (
        patch("daibai.api.auth._get_jwk_client") as mock_jwk,
        patch("daibai.api.auth.jwt.decode", return_value=mock_payload),
    ):
        mock_jwk.return_value.get_signing_key_from_jwt.return_value = mock_signing_key
        client = TestClient(app)
        response = client.get(
            "/api/settings",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code == 403
        assert "tenant" in response.json().get("detail", "").lower()
