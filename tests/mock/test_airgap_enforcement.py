import os
import jwt
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from daibai.api.auth import get_verified_user


class DummyKey:
    def __init__(self, key):
        self.key = key


def test_airgap_enforcement(monkeypatch):
    """
    Create two tokens:
    - allowed_token: tid == AUTH_TENANT_ID -> should pass
    - forbidden_token: tid == AZURE_TENANT_ID -> should be 403
    """
    os.environ["AUTH_TENANT_ID"] = "auth-tenant-123"
    os.environ["AZURE_TENANT_ID"] = "infra-tenant-999"

    secret = "test-secret"  # HS256 symmetric secret used for test

    # Monkeypatch PyJWKClient used in the auth module to return a dummy signing key
    import daibai.api.auth as authmod

    class FakeJWKClient:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            return DummyKey(secret)

    monkeypatch.setattr(authmod, "_get_jwk_client", lambda: FakeJWKClient("fake"))

    app = FastAPI()

    @app.get("/whoami")
    def whoami(user=Depends(get_verified_user)):
        return {"tid": user.get("tid"), "sub": user.get("sub")}

    client = TestClient(app)

    allowed_token = jwt.encode({"tid": "auth-tenant-123", "sub": "user1"}, secret, algorithm="HS256")
    forbidden_token = jwt.encode({"tid": "infra-tenant-999", "sub": "infra-admin"}, secret, algorithm="HS256")

    # Allowed token -> 200
    r = client.get("/whoami", headers={"Authorization": f"Bearer {allowed_token}"})
    assert r.status_code == 200
    assert r.json()["tid"] == "auth-tenant-123"

    # Forbidden token -> 403
    r2 = client.get("/whoami", headers={"Authorization": f"Bearer {forbidden_token}"})
    assert r2.status_code == 403

