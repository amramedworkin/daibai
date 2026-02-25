"""
Live test: Backend Robot-mediated validation accepts tokens from the Identity Plane.

Uses the Robot (AUTH_CLIENT_ID/SECRET) to create a test user, then acquires a user token
via ROPC (Resource Owner Password Credentials) and calls /api/settings. Expects 200 OK.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from daibai.api.server import app

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("AUTH_CLIENT_ID")
        and os.environ.get("AUTH_CLIENT_SECRET")
        and os.environ.get("AUTH_TENANT_ID")
    ),
    reason="Live backend auth test requires AUTH_CLIENT_ID, AUTH_CLIENT_SECRET, and AUTH_TENANT_ID.",
)


def _run_script(name: str, args=None, env=None, input_text=None, timeout=30):
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "entra" / name
    cmd = [str(script)]
    if args:
        cmd.extend(args)
    proc = subprocess.Popen(
        cmd,
        env=env or os.environ.copy(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


def _acquire_user_token(upn: str, password: str) -> str:
    """Acquire a user token via ROPC. Returns access_token or id_token."""
    tenant_id = os.environ.get("AUTH_TENANT_ID", "").strip()
    client_id = os.environ.get("AUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AUTH_CLIENT_SECRET", "").strip()
    tenant_name = os.environ.get("AUTH_TENANT_NAME", "daibaiauth").strip()
    authority_type = os.environ.get("AUTH_AUTHORITY_TYPE", "ciam").strip().lower()

    if authority_type == "azure":
        authority = f"https://login.microsoftonline.com/{tenant_id}"
    else:
        authority = f"https://{tenant_name}.ciamlogin.com/{tenant_id}"

    msal = pytest.importorskip("msal")
    app_msal = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"{authority}/",
    )
    result = app_msal.acquire_token_by_username_password(
        username=upn,
        password=password,
        scopes=["openid", "profile"],
    )
    if "error" in result:
        pytest.skip(f"ROPC not supported or failed: {result.get('error_description', result.get('error'))}")
    return result.get("id_token") or result.get("access_token", "")


def test_backend_accepts_identity_plane_token():
    """
    Robot creates a test user, we acquire a user token via ROPC, call /api/settings.
    Expects 200 OK when token is from the Identity Plane.
    """
    env = os.environ.copy()
    stamp = int(time.time())
    mail_nick = f"daibai_auth_{stamp}"
    password = f"TmpPass!{stamp}"

    # 1. Create user
    code, out, err = _run_script(
        "05_create_user.sh",
        args=["--display-name", f"Auth Test {stamp}", "--mail-nick", mail_nick, "--password", password],
        env=env,
    )
    if code != 0:
        pytest.skip(f"Could not create test user: {out} {err}")

    # 2. Parse UPN from create output (e.g. "upn: testuser@domain.onmicrosoft.com")
    upn = None
    for line in out.splitlines():
        if line.strip().startswith("upn:"):
            upn = line.split("upn:", 1)[1].strip()
            break
    if not upn:
        upn = f"{mail_nick}@daibaiauth.onmicrosoft.com"

    # 3. Acquire user token
    token = _acquire_user_token(upn, password)
    if not token:
        pytest.skip("Could not acquire user token (ROPC may not be enabled)")

    # 4. Call /api/settings
    client = TestClient(app)
    response = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    # 5. Cleanup: delete user
    _run_script("03_delete_single.sh", args=["--user", upn, "--execute"], env=env, input_text="y\n", timeout=60)
