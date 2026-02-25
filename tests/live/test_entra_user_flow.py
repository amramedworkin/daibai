import os
import subprocess
import time
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("AUTH_CLIENT_ID") and os.environ.get("AUTH_CLIENT_SECRET") and os.environ.get("AUTH_TENANT_ID")),
    reason="Live Entra user flow requires AUTH_CLIENT_ID, AUTH_CLIENT_SECRET, and AUTH_TENANT_ID environment variables.",
)


def _run_script(name: str, args=None, env=None, input_text=None, timeout=30):
    script = Path("scripts/entra") / name
    cmd = [str(script)]
    if args:
        cmd.extend(args)
    proc = subprocess.Popen(
        cmd, env=env or os.environ.copy(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


def test_entra_user_lifecycle_live():
    """
    Live test performing the full user lifecycle against the AUTH tenant:
    1) verify tenant
    2) list users
    3) create a new user
    4) list users and assert presence
    5) dry-run hard delete
    6) dry-run soft delete
    7) hard delete (real)
    8) list users and assert absence
    """
    env = os.environ.copy()

    # 1) Verify tenant
    code, out, err = _run_script("00_verify_tenant.sh", env=env)
    assert code == 0, f"verify tenant failed: out={out} err={err}"

    # 2) List users (baseline)
    code, out, err = _run_script("02_list_users.sh", env=env)
    assert code == 0, f"list users failed: out={out} err={err}"

    # 3) Create a new user (unique mail nick)
    stamp = int(time.time())
    mail_nick = f"daibai_test_{stamp}"
    password = f"TmpPass!{stamp}"
    args = ["--display-name", f"Daibai Test {stamp}", "--mail-nick", mail_nick, "--password", password]
    code, out, err = _run_script("05_create_user.sh", args=args, env=env)
    assert code == 0 and "User created" in out, f"create user failed: out={out} err={err}"

    # Construct expected UPN (script prints domain when creating; but we can infer by listing)
    upn = None
    # 4) List users and find the new user
    code, out, err = _run_script("02_list_users.sh", env=env)
    assert code == 0, f"list after create failed: out={out} err={err}"
    for line in out.splitlines():
        if mail_nick in line or f"@{mail_nick}" in line:
            # attempt to extract upn-like token
            parts = line.split()
            for p in parts:
                if "@" in p and mail_nick in p:
                    upn = p.strip()
                    break
        if upn:
            break
    assert upn is not None, f"Could not find created user in listing. Listing output:\n{out}"

    # 5) Hard delete dry-run (no --execute) -> expect DRY-RUN
    code, out, err = _run_script("03_delete_single.sh", args=["--user", upn], env=env)
    assert ("DRY-RUN" in out or "would DELETE" in out), f"hard delete dry-run unexpected output: out={out} err={err}"

    # 6) Soft delete dry-run
    code, out, err = _run_script("03_delete_single.sh", args=["--soft", "--user", upn], env=env)
    assert ("DRY-RUN" in out or "would DELETE" in out or "soft-deleted" not in out), f"soft delete dry-run unexpected: out={out} err={err}"

    # 7) Hard delete real (execute). The script will prompt; provide "y\n"
    code, out, err = _run_script("03_delete_single.sh", args=["--user", upn, "--execute"], env=env, input_text="y\n", timeout=60)
    assert code == 0, f"real delete failed: out={out} err={err}"

    # 8) List users and ensure the UPN no longer present
    code, out, err = _run_script("02_list_users.sh", env=env)
    assert code == 0, f"final list failed: out={out} err={err}"
    assert upn not in out, f"user {upn} still present after deletion. Listing:\n{out}"
