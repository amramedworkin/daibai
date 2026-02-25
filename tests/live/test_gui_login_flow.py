"""
Live test: GUI Auth Plane integration.

Verifies that:
- GET /api/auth-config returns MSAL configuration with authority pointing at AUTH_TENANT_ID
- The authority URL is correctly formed for the Identity Plane

Manual verification: With the server running, open the GUI and click Login.
You should be redirected to the DaiBai sign-in page (daibaiauth.ciamlogin.com or
login.microsoftonline.com depending on AUTH_AUTHORITY_TYPE).
"""

import os
import pytest

from fastapi.testclient import TestClient

from daibai.api.server import app


pytestmark = pytest.mark.skipif(
    not os.environ.get("AUTH_TENANT_ID"),
    reason="Live auth test requires AUTH_TENANT_ID environment variable.",
)


def test_auth_config_endpoint_returns_identity_plane_config():
    """
    Verifies /api/auth-config returns MSAL config with authority for the Identity Plane.
    The frontend uses this to point MSAL at the correct directory for logins.
    """
    client = TestClient(app)
    response = client.get("/api/auth-config")
    assert response.status_code == 200
    data = response.json()
    assert "auth_tenant_id" in data
    assert "auth_client_id" in data
    assert "authority" in data
    assert data["auth_tenant_id"] == os.environ.get("AUTH_TENANT_ID", "").strip()
    # Authority must contain the tenant ID
    assert data["auth_tenant_id"] in data["authority"]
    # Authority must be a valid URL
    assert data["authority"].startswith("https://")


def test_auth_config_authority_format():
    """Authority is either ciamlogin.com (CIAM) or login.microsoftonline.com (Azure AD)."""
    client = TestClient(app)
    response = client.get("/api/auth-config")
    assert response.status_code == 200
    authority = response.json()["authority"]
    is_ciam = "ciamlogin.com" in authority
    is_azure = "login.microsoftonline.com" in authority
    assert is_ciam or is_azure, f"Authority must be CIAM or Azure AD format: {authority}"
