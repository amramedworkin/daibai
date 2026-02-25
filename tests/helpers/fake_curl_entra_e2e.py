#!/usr/bin/env python3
"""
Stateful fake curl for Entra tenant E2E tests.
Simulates: identity, list users, create user, list (with new user), delete user.
Reads state from ENTRATEST_STATE_FILE; updates it after each call.
"""
import json
import os
import sys

STATE_FILE = os.environ.get("ENTRATEST_STATE_FILE", "")
DOMAIN = "daibaiauth.onmicrosoft.com"
NEW_USER_UPN = "e2etest@daibaiauth.onmicrosoft.com"
NEW_USER_ID = "e2e-test-user-object-id-12345"


def _read_state():
    if not STATE_FILE or not os.path.exists(STATE_FILE):
        return {"get_users_count": 0, "user_created": False}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"get_users_count": 0, "user_created": False}


def _write_state(s):
    if STATE_FILE:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(s, f)
        except Exception:
            pass


def main():
    args = sys.argv[1:]
    state = _read_state()

    # Find URL and HTTP method
    url = ""
    is_post = False
    is_delete = False
    wants_status = "-w" in args or "\n%{http_code}" in " ".join(args)
    for i, a in enumerate(args):
        if a == "-X" and i + 1 < len(args):
            method = args[i + 1].upper()
            if method == "POST":
                is_post = True
            elif method == "DELETE":
                is_delete = True
        if a.startswith("https://"):
            url = a
            break

    def out(body: str, status: int = 200):
        print(body, end="")
        if wants_status:
            print(f"\n{status}", end="")
        print()

    # Token
    if "/oauth2/v2.0/token" in url:
        out('{"access_token":"FAKE_TOKEN"}')
        return 0

    # Organization
    if "/organization" in url and "/domains" not in url:
        out('{"value":[{"displayName":"DaiBai Customers"}]}')
        return 0

    # Domains
    if "/domains" in url and "/deletedItems" not in url:
        out(f'{{"value":[{{"id":"{DOMAIN}","isDefault":true}}]}}')
        return 0

    # GET user by UPN (resolve id) - must be before list check (/users/email vs /users?)
    if "/v1.0/users/" in url and "?" in url and "/deletedItems" not in url and not is_post and not is_delete:
        # URL like /v1.0/users/email@domain.com?$select=id
        if "/users/" in url.split("?")[0] and url.split("?")[0].split("/users/")[-1]:
            out(f'{{"id":"{NEW_USER_ID}","userPrincipalName":"{NEW_USER_UPN}"}}')
            return 0

    # GET users (list)
    if "/users" in url and "?" in url and not is_post and not is_delete:
        state["get_users_count"] = state.get("get_users_count", 0) + 1
        _write_state(state)
        if state.get("user_created"):
            users = [{
                "displayName": "E2E Test User",
                "userPrincipalName": NEW_USER_UPN,
                "userType": "Member",
                "accountEnabled": True,
            }]
        else:
            users = []
        out(json.dumps({"value": users}))
        return 0

    # POST users (create)
    if "/v1.0/users" in url and "?" not in url and is_post:
        state["user_created"] = True
        _write_state(state)
        created = {
            "id": NEW_USER_ID,
            "displayName": "E2E Test User",
            "userPrincipalName": NEW_USER_UPN,
        }
        out(json.dumps(created), 201)
        return 0

    # DELETE user or deletedItems
    if is_delete and ("/users/" in url or "/deletedItems/" in url):
        out("", 204)
        return 0

    out("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
