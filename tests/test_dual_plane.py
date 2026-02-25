import os
import pytest
from unittest import mock

from daibai.core.config import Config
from daibai.core.env_ready import EnvValidator
from daibai.core.identity import IdentityManager, SecurityAirgapException


def test_dual_plane_config_separation():
    """Ensures configuration correctly separates the two distinct tenants."""
    test_env = {
        "AUTH_TENANT_ID": "identity_tenant_123",
        "AZURE_TENANT_ID": "infra_tenant_456",
        "AUTH_CLIENT_ID": "client_abc",
    }
    with mock.patch.dict(os.environ, test_env, clear=True):
        config = Config()
        assert config.auth_tenant_id == "identity_tenant_123"
        assert config.azure_tenant_id == "infra_tenant_456"
        assert config.auth_tenant_id != config.azure_tenant_id


def test_env_preflight_blocks_missing_planes():
    """Ensures the app will not boot if the dual-plane boundaries are missing."""
    invalid_env = {
        "DB_HOST": "localhost",
        "GEMINI_API_KEY": "valid_key",
        "AZURE_TENANT_ID": "infra_tenant_456",
        # Missing AUTH_TENANT_ID and AUTH_CLIENT_ID
    }
    with mock.patch.dict(os.environ, invalid_env, clear=True):
        is_valid, issues = EnvValidator.validate()
        assert is_valid is False
        assert "AUTH_TENANT_ID" in issues or "AUTH_CLIENT_ID" in issues


def test_airgap_credential_boundary():
    """
    Proves that infrastructure initialization strictly rejects any user-plane tokens,
    ensuring the backend never authenticates using inbound user credentials.
    """
    mock_user_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    infra_tenant = "infra_tenant_456"
    with pytest.raises(SecurityAirgapException) as excinfo:
        IdentityManager.enforce_airgap(user_token=mock_user_jwt, infra_tenant_id=infra_tenant)
    assert "CRITICAL: Attempted to pass User Identity Token" in str(excinfo.value)


@mock.patch("daibai.core.identity.DefaultAzureCredential")
def test_infrastructure_credential_scoping(mock_credential):
    """Ensures the Managed Identity credential is hard-bound to the infrastructure tenant."""
    IdentityManager.get_infrastructure_credential(azure_tenant_id="infra_tenant_456")
    mock_credential.assert_called_once_with(additionally_allowed_tenants=[])
    assert os.environ.get("AZURE_TENANT_ID") == "infra_tenant_456"

