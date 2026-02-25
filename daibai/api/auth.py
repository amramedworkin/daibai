from typing import Dict, Any
import os
import requests
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient

security = HTTPBearer(auto_error=True)

# cache PyJWKClient per tenant
_JWK_CLIENTS: Dict[str, PyJWKClient] = {}


def _get_jwk_client(tenant: str) -> PyJWKClient:
    if tenant in _JWK_CLIENTS:
        return _JWK_CLIENTS[tenant]
    jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    client = PyJWKClient(jwks_url)
    _JWK_CLIENTS[tenant] = client
    return client


def get_verified_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    FastAPI dependency that verifies an incoming Bearer JWT comes from the
    configured Identity Plane (AUTH_TENANT_ID) and not from the Infrastructure Plane (AZURE_TENANT_ID).

    Behavior:
    - Verifies JWT signature using Microsoft JWKS for AUTH_TENANT_ID.
    - Extracts the 'tid' claim.
    - If tid == AZURE_TENANT_ID -> 403 Forbidden.
    - If tid != AUTH_TENANT_ID -> 403 Forbidden.
    - On token verification errors -> 401 Unauthorized.
    """
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    auth_tenant = os.environ.get("AUTH_TENANT_ID", "").strip()
    infra_tenant = os.environ.get("AZURE_TENANT_ID", "").strip()
    if not auth_tenant:
        # If not configured, fail closed
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="AUTH_TENANT_ID not configured")

    try:
        # Obtain signing key via PyJWKClient (fetches Microsoft JWKS)
        jwk_client = _get_jwk_client(auth_tenant)
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        # jwt.decode will verify signature and standard claims; audience verification is skipped here.
        decoded = jwt.decode(token, signing_key.key, algorithms=["RS256", "RS384", "RS512", "HS256"], options={"verify_aud": False})
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {str(exc)}")

    tid = decoded.get("tid") or decoded.get("tenant")
    if not tid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing 'tid' claim")

    if infra_tenant and tid == infra_tenant:
        # Tokens from Infrastructure Plane are forbidden to access Chat API
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token from Infrastructure tenant is not allowed")

    if tid != auth_tenant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token tenant mismatch")

    return decoded

