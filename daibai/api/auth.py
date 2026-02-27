"""
Firebase Authentication dependency for FastAPI.

Verifies Firebase ID tokens sent as Bearer tokens by the frontend.
On first successful verification for a given uid, calls ensure_user_exists()
to perform Just-in-Time user registration into Cosmos DB.

Requirements:
    pip install firebase-admin

Service account credentials:
    Set FIREBASE_SERVICE_ACCOUNT_JSON to the path of your downloaded service
    account key JSON file, or set GOOGLE_APPLICATION_CREDENTIALS.
    If neither is set, falls back to Application Default Credentials (ADC),
    which works in GCP/Cloud Run environments automatically.
"""

import os
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)

# Lazy singleton: initialise firebase_admin only once.
_firebase_app = None


def _get_firebase_app():
    """Initialise and return the firebase_admin App singleton."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials

        key_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
        if key_path and os.path.isfile(key_path):
            cred = credentials.Certificate(key_path)
        else:
            # Fall back to Application Default Credentials (ADC).
            # Works automatically in GCP / Cloud Run; for local dev, run:
            #   gcloud auth application-default login
            cred = credentials.ApplicationDefault()

        _firebase_app = firebase_admin.initialize_app(cred)
    except ImportError:
        # firebase-admin not installed — token verification will be skipped.
        # Run: pip install firebase-admin
        _firebase_app = None
    except Exception as exc:
        print(f"[auth] Firebase Admin init failed: {exc}", flush=True)
        _firebase_app = None

    return _firebase_app


def verify_firebase_token(id_token: str) -> Dict[str, Any]:
    """
    Verify a Firebase ID token and return the decoded claims dict.
    Raises HTTPException 401 on any failure.
    """
    app = _get_firebase_app()
    if app is None:
        # firebase-admin unavailable — perform an unverified decode for dev mode.
        # TODO: remove this fallback once firebase-admin is installed.
        try:
            import jwt as _pyjwt
            claims = _pyjwt.decode(id_token, options={"verify_signature": False})
            claims.setdefault("uid", claims.get("user_id") or claims.get("sub", ""))
            claims.setdefault("email", "")
            print("[auth] WARNING: token signature not verified (firebase-admin missing)", flush=True)
            return claims
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        import firebase_admin.auth as fb_auth
        decoded = fb_auth.verify_id_token(id_token, app=app)
        return decoded
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {exc}",
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency that:
    1. Extracts the Bearer token from the Authorization header.
    2. Verifies the Firebase ID token.
    3. Calls ensure_user_exists() for Just-in-Time user registration.
    4. Returns the decoded token claims for downstream route handlers.

    Usage in a route:
        @app.get("/api/protected")
        async def protected(user: dict = Depends(get_current_user)):
            return {"uid": user["uid"]}
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    token = credentials.credentials
    claims = verify_firebase_token(token)

    uid: str = claims.get("uid") or claims.get("user_id") or claims.get("sub", "")
    email: str = claims.get("email", "")

    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing uid claim",
        )

    # Just-in-Time user registration: create the Cosmos DB profile on first sign-in.
    try:
        from .database import CosmosStore
        store = CosmosStore()
        if store.is_configured:
            await store.ensure_user_exists(user_id=uid, email=email)
    except Exception as exc:
        # Non-fatal: log and continue — don't block authentication if Cosmos is
        # temporarily unavailable or not configured in the current environment.
        print(f"[auth] ensure_user_exists skipped: {exc}", flush=True)

    return {"uid": uid, "email": email, "claims": claims}
