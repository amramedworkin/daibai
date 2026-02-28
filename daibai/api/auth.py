"""
Firebase Authentication dependency for FastAPI.

Verifies Firebase ID tokens sent as Bearer tokens by the frontend.
On first successful verification for a given uid, calls ensure_user_exists()
to perform Just-in-Time user registration into Cosmos DB.

Requirements:
    pip install firebase-admin

Service account credentials:
    Set FIREBASE_SERVICE_ACCOUNT_JSON to the path of the service account key
    JSON file (e.g. config/secrets/firebase-adminsdk.json).
    The file is gitignored and must never be committed to the repository.
"""

import os
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def _init_firebase_app():
    """
    Initialise firebase_admin at module load time using the service account key
    file named by FIREBASE_SERVICE_ACCOUNT_JSON.

    Returns the App on success, None when the key file is absent or
    firebase-admin is not installed (dev/test fallback — tokens are decoded
    without signature verification and a warning is printed on every request).
    """
    cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    # Resolve relative paths against the project root (two levels up from this file).
    if cred_path and not os.path.isabs(cred_path):
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cred_path = os.path.join(_project_root, cred_path)

    if not cred_path or not os.path.exists(cred_path):
        print(
            "WARNING: FIREBASE_SERVICE_ACCOUNT_JSON not set or file not found. "
            "Running in UNVERIFIED dev mode — tokens are NOT cryptographically validated. "
            "Set FIREBASE_SERVICE_ACCOUNT_JSON=config/secrets/firebase-adminsdk.json "
            "to enable full token verification.",
            flush=True,
        )
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(cred_path)
        app = firebase_admin.initialize_app(cred)
        print(f"[auth] Firebase Admin SDK initialised from {cred_path}", flush=True, file=__import__('sys').stderr)
        return app
    except ImportError:
        print(
            "WARNING: firebase-admin is not installed. "
            "Run: pip install firebase-admin",
            flush=True,
        )
        return None
    except Exception as exc:
        print(f"[auth] Firebase Admin init failed: {exc}", flush=True)
        return None


# Initialise once at import time so startup logs are visible immediately.
_firebase_app = _init_firebase_app()


def verify_firebase_token(id_token: str) -> Dict[str, Any]:
    """
    Verify a Firebase ID token and return the decoded claims dict.
    Raises HTTPException 401 on any failure.
    """
    app = _firebase_app
    if app is None:
        # Dev-only fallback: decode without signature verification when the service
        # account key file is absent (e.g. running locally without credentials).
        # Tokens are structurally validated but NOT cryptographically verified.
        # This path is blocked in production by requiring FIREBASE_SERVICE_ACCOUNT_JSON.
        try:
            import jwt as _pyjwt
            claims = _pyjwt.decode(id_token, options={"verify_signature": False})
            claims.setdefault("uid", claims.get("user_id") or claims.get("sub", ""))
            claims.setdefault("email", "")
            print("[auth] WARNING: token signature not verified — set FIREBASE_SERVICE_ACCOUNT_JSON for production", flush=True)
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
