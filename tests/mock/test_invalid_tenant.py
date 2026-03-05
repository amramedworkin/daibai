"""
Mock test: Token from a non-DaiBai tenant returns 403 Forbidden.

Auth uses Firebase (firebase_admin.auth.verify_id_token), not JWK/JWT. Tenant (tid)
checks were part of an older design. Skipped until tenant validation is reintroduced.
"""

import pytest

from fastapi.testclient import TestClient

from daibai.api.server import app


@pytest.mark.skip(reason="Auth uses Firebase, not JWK; tenant (tid) check not implemented")
def test_token_from_wrong_tenant_returns_403():
    """
    A token that passes signature verification but has tid from a different tenant
    must be rejected with 403 Forbidden.
    """
    client = TestClient(app)
    response = client.get(
        "/api/settings",
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 403
    assert "tenant" in response.json().get("detail", "").lower()
