import os
from typing import Optional

try:
    from azure.identity import DefaultAzureCredential
except Exception:  # pragma: no cover - azure identity optional in tests
    DefaultAzureCredential = None  # type: ignore


class SecurityAirgapException(Exception):
    """Raised when an identity boundary is crossed."""
    pass


class IdentityManager:
    """Enforces the Dual-Plane Identity Architecture."""

    @staticmethod
    def validate_user_token(token: str, auth_tenant_id: str, auth_client_id: str) -> bool:
        """
        IDENTITY PLANE: Validates an inbound user JWT against the Identity Tenant.
        (Stubbed: in production this should verify signature, issuer, audience via JWKS.)
        """
        if not auth_tenant_id or not auth_client_id:
            raise ValueError("Identity Plane configuration missing.")
        # TODO: implement real JWT validation (jwks fetch & verify)
        return True

    @staticmethod
    def get_infrastructure_credential(azure_tenant_id: str):
        """
        INFRASTRUCTURE PLANE: Retrieves the Managed Identity credential for backend services.
        AIRGAP ENFORCEMENT: Strictly binds the credential to the Infrastructure Tenant.
        """
        if not azure_tenant_id:
            raise ValueError("Infrastructure Plane configuration missing.")

        # Explicitly bind the tenant for downstream SDKs
        os.environ["AZURE_TENANT_ID"] = azure_tenant_id
        if DefaultAzureCredential is None:
            raise RuntimeError("azure-identity not available in this environment")
        # additionally_allowed_tenants=[] prevents cross-tenant token acquisition
        return DefaultAzureCredential(additionally_allowed_tenants=[])

    @staticmethod
    def enforce_airgap(user_token: Optional[str], infra_tenant_id: str):
        """
        Validates that a user token is NEVER passed to an infrastructure client.
        """
        if user_token is not None:
            raise SecurityAirgapException("CRITICAL: Attempted to pass User Identity Token to Infrastructure Plane.")
        return True

