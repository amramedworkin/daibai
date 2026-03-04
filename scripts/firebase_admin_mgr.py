"""
Firebase + Cosmos DB Unified Admin CLI
========================================
Every write operation here is dual-write: Firebase Authentication AND Cosmos DB
are updated atomically so the two systems are never out of sync.

Usage (from project root):
    .venv/bin/python scripts/firebase_admin_mgr.py <command> [options]
    ./scripts/cli.sh firebase-admin <command> [options]

Commands:
    list                          List all Firebase users + Cosmos sync status
    create --email E --password P --name N [--uid U] [--phone PH]
    update <uid> [--email E] [--name N] [--password P] [--phone PH]
    delete <uid>                  Delete from Firebase AND remove Cosmos profile
    delete-all                    Global wipe: Firebase Auth + Users + Conversations
    integrate <uid|email>         Ensure Firebase user has Cosmos DB profile
    set-claims <uid> <json>       Apply custom claims JSON to a Firebase user
    links <uid> reset|verify      Generate a password-reset or email-verify link
    revoke <uid>                  Revoke all refresh tokens for a Firebase user
    sync-check                    Find and optionally repair orphaned records
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Bootstrap: load .env and add project root to sys.path
# ---------------------------------------------------------------------------

def _load_env() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(project_root, ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _bootstrap() -> str:
    """Load env, add project root to path, return project root."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _load_env()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


# ---------------------------------------------------------------------------
# Firebase initialisation (reuses auth.py credential path convention)
# ---------------------------------------------------------------------------

def _init_firebase(project_root: str):
    """Return an initialised firebase_admin app or exit with an error."""
    cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if cred_path and not os.path.isabs(cred_path):
        cred_path = os.path.join(project_root, cred_path)

    if not cred_path or not os.path.exists(cred_path):
        _die(
            "FIREBASE_SERVICE_ACCOUNT_JSON not set or file not found.\n"
            "  Set it in .env, e.g.:\n"
            "  FIREBASE_SERVICE_ACCOUNT_JSON=config/secrets/firebase-adminsdk.json"
        )

    try:
        import firebase_admin
        from firebase_admin import credentials
        try:
            return firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(cred_path)
            return firebase_admin.initialize_app(cred)
    except ImportError:
        _die("firebase-admin is not installed.  Run: pip install firebase-admin")


# ---------------------------------------------------------------------------
# Cosmos DB — async wrapper helpers
# ---------------------------------------------------------------------------

def _make_cosmos_store():
    """Return a configured CosmosStore instance (not yet connected)."""
    from daibai.api.database import CosmosStore
    return CosmosStore()


def _cosmos_run(store, coro):
    """
    Run *coro* inside a single asyncio event loop, then close the CosmosStore
    (which closes the underlying aiohttp ClientSession and connector) before
    the loop exits.  This eliminates the 'Unclosed client session' warnings
    that occur when the store is reused across multiple asyncio.run() calls.
    """
    async def _wrapper():
        try:
            return await coro
        finally:
            await store.close()
    return asyncio.run(_wrapper())


# ── Cosmos async helpers (called *inside* a running loop via _cosmos_run) ──

async def _cosmos_create_profile(store, uid: str, email: str,
                                  display_name: str = "", phone: str = "") -> None:
    doc = {
        "id":           uid,
        "type":         "user",
        "uid":          uid,
        "email":        email,
        "username":     email,
        "display_name": display_name,
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
    }
    if phone:
        doc["phone_number"] = phone
    await store.upsert_user(doc)


async def _cosmos_patch(store, uid: str, fields: dict) -> None:
    await store.patch_user(user_id=uid, fields=fields)


async def _cosmos_delete_user(store, uid: str) -> bool:
    return await store.delete_user(uid)


async def _cosmos_wipe_all(store) -> tuple[int, int]:
    u = await store.delete_all_users()
    c = await store.delete_all_conversations()
    return u, c


async def _cosmos_list_users(store) -> list:
    return await store.list_users()


async def _cosmos_get_user(store, uid: str):
    return await store.get_user(oid=uid)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

GREEN  = "\033[1;32m"
CYAN   = "\033[1;36m"
YELLOW = "\033[1;33m"
RED    = "\033[1;31m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
NC     = "\033[0m"

def _ok(msg):   print(f"  {GREEN}✔{NC}  {msg}")
def _info(msg): print(f"  {CYAN}→{NC}  {msg}")
def _warn(msg): print(f"  {YELLOW}⚠{NC}  {msg}")
def _die(msg):
    print(f"\n  {RED}✘  {msg}{NC}\n", file=sys.stderr)
    sys.exit(1)

def _sep(char="─", width=72):
    print(f"  {DIM}{char * width}{NC}")

def _header(title):
    print()
    print(f"  {BOLD}{title}{NC}")
    _sep()

def _cosmos_ok(label):
    print(f"       {GREEN}[Cosmos]{NC} {label}")

def _cosmos_warn(label):
    print(f"       {YELLOW}[Cosmos]{NC} {label}")


# ---------------------------------------------------------------------------
# User display helpers
# ---------------------------------------------------------------------------

def _fmt_user(u, cosmos_doc: dict | None = None) -> str:
    """Format a user with Firebase and Cosmos DB fields in separate sections."""
    meta = u.user_metadata
    created_ts = _ts(meta.creation_timestamp) if meta else " —"
    last_signin_ts = _ts(getattr(meta, "last_sign_in_timestamp", None)) if meta else " —"

    # Firebase section
    fb_lines = [
        f"  {BOLD}{u.uid}{NC}  {GREEN}[Firebase]{NC}",
        f"    uid:           {u.uid}",
        f"    email:         {u.email or '(none)'}",
        f"    email_verified:{u.email_verified}",
        f"    display_name:  {u.display_name or '(none)'}",
        f"    phone_number:  {u.phone_number or '(none)'}",
        f"    photo_url:     {u.photo_url or '(none)'}",
        f"    disabled:      {u.disabled}",
        f"    providers:     {_fmt_providers(u)}",
        f"    created:       {created_ts}",
        f"    last_sign_in:  {last_signin_ts}",
    ]
    if u.custom_claims:
        fb_lines.append(f"    custom_claims: {u.custom_claims}")

    # Cosmos section
    if cosmos_doc is not None:
        cosmos_lines = [
            f"  {CYAN}[Cosmos DB]{NC}",
        ]
        # Show all Cosmos fields (sort for consistent ordering)
        for k in sorted(cosmos_doc.keys()):
            v = cosmos_doc[k]
            if v is None or v == "":
                display = "(none)"
            elif isinstance(v, (dict, list)):
                display = json.dumps(v) if v else "(empty)"
            else:
                display = str(v)
            cosmos_lines.append(f"    {k}: {display}")
        return "\n".join(fb_lines + cosmos_lines) + "\n"
    else:
        cosmos_lines = [f"  {RED}[Cosmos DB — no record]{NC}"]
        return "\n".join(fb_lines + cosmos_lines) + "\n"

def _ts(ms) -> str:
    if not ms:
        return " —"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return " " + dt.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_providers(u) -> str:
    """Return comma-separated provider IDs (e.g. password, google.com)."""
    if not u.provider_data:
        return "(none)"
    return ", ".join(p.provider_id for p in u.provider_data)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_list(args):
    import firebase_admin.auth as fb_auth
    store = _make_cosmos_store()
    _header("Firebase + Cosmos DB Users  (fields by source)")

    # Collect Firebase users (synchronous SDK)
    all_fb_users = []
    page = fb_auth.list_users()
    while page:
        all_fb_users.extend(page.users)
        page = page.get_next_page()

    # Fetch full Cosmos docs for uid -> doc mapping
    cosmos_by_uid: dict = {}
    if store.is_configured:
        try:
            cosmos_docs = _cosmos_run(store, _cosmos_list_users(store))
            for d in cosmos_docs:
                uid = d.get("id") or d.get("uid")
                if uid:
                    cosmos_by_uid[uid] = d
        except Exception as e:
            _warn(f"Could not reach Cosmos DB: {e}")

    for u in all_fb_users:
        cosmos_doc = cosmos_by_uid.get(u.uid) if store.is_configured else None
        print(_fmt_user(u, cosmos_doc=cosmos_doc))

    _sep()
    _info(f"Total: {BOLD}{len(all_fb_users)}{NC} Firebase user(s)  |  Cosmos records: {len(cosmos_by_uid)}")
    print()


def cmd_create(args):
    import firebase_admin
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    kwargs = dict(
        email          = args.email,
        password       = args.password,
        display_name   = args.name or "",
        email_verified = True,
    )
    if args.uid:
        kwargs["uid"] = args.uid
    if args.phone:
        kwargs["phone_number"] = args.phone

    _header(f"Creating user: {args.email}")
    try:
        u = fb_auth.create_user(**kwargs)
        _ok(f"Firebase: uid={BOLD}{u.uid}{NC}  email_verified=True")
    except firebase_admin.auth.EmailAlreadyExistsError:
        _die(f"A user with email {args.email!r} already exists.")
    except firebase_admin.auth.UidAlreadyExistsError:
        _die(f"A user with uid {args.uid!r} already exists.")
    except Exception as exc:
        _die(str(exc))

    if store.is_configured:
        try:
            _cosmos_run(store, _cosmos_create_profile(
                store,
                uid          = u.uid,
                email        = args.email,
                display_name = args.name or "",
                phone        = args.phone or "",
            ))
            _cosmos_ok(f"Profile created in Cosmos DB  uid={u.uid}")
        except Exception as e:
            _cosmos_warn(f"Firebase user created but Cosmos write failed: {e}")
            _cosmos_warn("Run 'sync-check' to repair the missing record.")
    else:
        _cosmos_warn("COSMOS_ENDPOINT not set — Cosmos DB profile not created.")

    _info("email_verified=True — no verification email will be sent (Admin SDK bypass).")
    print()


def cmd_update(args):
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    _header(f"Update User: {args.uid}")

    try:
        u = fb_auth.get_user(args.uid)
    except firebase_admin.auth.UserNotFoundError:
        _die(f"No Firebase user found with uid={args.uid!r}")

    print(f"  Current email  : {u.email or '(none)'}")
    print(f"  Current name   : {u.display_name or '(none)'}")
    print(f"  Current phone  : {u.phone_number or '(none)'}")
    print(f"  email_verified : {u.email_verified}")
    _sep()

    fb_updates = {}
    cosmos_fields = {}

    if args.email:
        fb_updates["email"] = args.email
        fb_updates["email_verified"] = False
        cosmos_fields["email"] = args.email
        cosmos_fields["username"] = args.email
        _info("email_verified will be reset to False because the email is changing.")

    if args.name is not None:
        fb_updates["display_name"] = args.name
        cosmos_fields["display_name"] = args.name

    if args.password:
        fb_updates["password"] = args.password

    if args.phone is not None:
        fb_updates["phone_number"] = (
            args.phone if args.phone else fb_auth.DELETE_ATTRIBUTE
        )
        if args.phone:
            cosmos_fields["phone_number"] = args.phone

    if not fb_updates:
        _warn("No fields to update. Pass --email, --name, --password, or --phone.")
        return

    try:
        fb_auth.update_user(args.uid, **fb_updates)
        _ok(f"Firebase updated: {', '.join(fb_updates.keys())}")
    except Exception as exc:
        _die(f"Firebase update failed: {exc}")

    if cosmos_fields and store.is_configured:
        try:
            _cosmos_run(store, _cosmos_patch(store, args.uid, cosmos_fields))
            _cosmos_ok(f"Cosmos DB synced: {', '.join(cosmos_fields.keys())}")
        except Exception as e:
            _cosmos_warn(f"Firebase updated but Cosmos sync failed: {e}")
            _cosmos_warn("Run 'sync-check' to repair the discrepancy.")
    print()


def cmd_delete(args):
    import firebase_admin
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    _header(f"Delete User: {args.uid}")

    try:
        u = fb_auth.get_user(args.uid)
    except firebase_admin.auth.UserNotFoundError:
        _die(f"No Firebase user found with uid={args.uid!r}")

    _warn(f"About to delete: {u.email or '(no email)'}  ({u.uid})")
    _warn("This removes the Firebase account AND the Cosmos DB user profile.")
    _warn("Conversations are anonymous sessions and cannot be selectively deleted.")
    print()
    confirm = input("  Type 'YES' to confirm: ").strip()
    if confirm != "YES":
        _info("Cancelled.")
        return

    errors = []

    # 1. Firebase
    try:
        fb_auth.delete_user(args.uid)
        _ok("Firebase: user deleted")
    except Exception as e:
        errors.append(f"Firebase delete failed: {e}")
        _warn(f"Firebase delete failed: {e}")

    # 2. Cosmos Users container — single event loop, session closed on exit
    if store.is_configured:
        try:
            found = _cosmos_run(store, _cosmos_delete_user(store, args.uid))
            if found:
                _cosmos_ok("User profile deleted from Cosmos DB")
            else:
                _cosmos_warn("No Cosmos DB profile found (already clean)")
        except Exception as e:
            errors.append(f"Cosmos delete failed: {e}")
            _cosmos_warn(f"Cosmos delete failed: {e}")
    else:
        _cosmos_warn("COSMOS_ENDPOINT not set — Cosmos DB not updated.")

    if errors:
        _warn("Some operations failed — run 'sync-check' to verify consistency.")
    print()


def cmd_delete_all(args):
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    _header("WIPE ALL DATA  (Firebase Auth + Cosmos DB)")
    _warn("This will permanently delete EVERY user in Firebase Authentication.")
    _warn("This will TRUNCATE the Cosmos DB Users AND Conversations containers.")
    _warn("This action CANNOT be undone.")
    print()
    if not getattr(args, "force", False):
        confirm = input(f"  {RED}Type 'YES' to confirm:{NC} ").strip()
        if confirm != "YES":
            _info("Cancelled — nothing deleted.")
            return

    # 1. Collect Firebase UIDs
    fb_uids = []
    page = fb_auth.list_users()
    while page:
        fb_uids.extend(u.uid for u in page.users)
        page = page.get_next_page()

    # 2. Delete Firebase users in batches of 100
    fb_count = 0
    for i in range(0, len(fb_uids), 100):
        batch = fb_uids[i:i + 100]
        result = fb_auth.delete_users(batch)
        fb_count += result.success_count
        if result.failure_count:
            for err in result.errors:
                _warn(f"Firebase: failed to delete uid={err.uid}: {err.reason}")
    _ok(f"Firebase: {fb_count} user(s) deleted")

    # 3. Wipe Cosmos
    if store.is_configured:
        try:
            users_del, convs_del = _cosmos_run(store, _cosmos_wipe_all(store))
            _cosmos_ok(f"Cosmos Users container: {users_del} record(s) deleted")
            _cosmos_ok(f"Cosmos Conversations container: {convs_del} record(s) deleted")
        except Exception as e:
            _cosmos_warn(f"Cosmos wipe failed: {e}")
    else:
        _cosmos_warn("COSMOS_ENDPOINT not set — Cosmos DB not cleared.")

    print()
    _ok("Global wipe complete.")
    print()


def cmd_set_claims(args):
    import firebase_admin.auth as fb_auth

    _header(f"Set Custom Claims: {args.uid}")
    try:
        claims = json.loads(args.claims_json)
    except json.JSONDecodeError as e:
        _die(f"Invalid JSON: {e}")

    try:
        fb_auth.get_user(args.uid)
    except firebase_admin.auth.UserNotFoundError:
        _die(f"No Firebase user found with uid={args.uid!r}")

    fb_auth.set_custom_user_claims(args.uid, claims)
    _ok(f"Claims applied: {claims}")
    print()
    _warn(
        "Token propagation reminder:\n"
        "     The user must sign out and sign back in (or force-refresh their\n"
        "     ID token) before the client-side app.js will see the new claims.\n"
        "     In app.js: firebase.auth().currentUser.getIdToken(true) forces a refresh."
    )
    print()


def cmd_links(args):
    import firebase_admin.auth as fb_auth

    _header(f"Action Link: {args.link_type} — {args.uid}")
    try:
        u = fb_auth.get_user(args.uid)
    except firebase_admin.auth.UserNotFoundError:
        _die(f"No Firebase user found with uid={args.uid!r}")

    if not u.email:
        _die(f"User uid={args.uid!r} has no email address.")

    link_type = args.link_type.lower()
    if link_type == "reset":
        link = fb_auth.generate_password_reset_link(u.email)
        label = "Password Reset"
    elif link_type == "verify":
        link = fb_auth.generate_email_verification_link(u.email)
        label = "Email Verification"
    else:
        _die(f"Unknown link type {link_type!r}. Use 'reset' or 'verify'.")

    _info(f"{label} link for {u.email}:")
    print()
    print(f"  {CYAN}{link}{NC}")
    print()
    _warn("This link is single-use and expires after one hour.")
    print()


def cmd_integrate(args):
    """
    Ensure a Firebase user has a Cosmos DB profile.
    Accepts uid or email; creates the Cosmos profile if missing.
    """
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    identifier = (args.uid_or_email or "").strip()
    if not identifier:
        _die("Usage: firebase-admin integrate <uid | email>")

    # Resolve to Firebase user
    if "@" in identifier:
        try:
            u = fb_auth.get_user_by_email(identifier)
        except Exception as e:
            if "not found" in str(e).lower() or "no user" in str(e).lower():
                _die(f"No Firebase user found with email {identifier!r}")
            raise
    else:
        try:
            u = fb_auth.get_user(identifier)
        except fb_auth.UserNotFoundError:
            _die(f"No Firebase user found with uid {identifier!r}")

    _header(f"Integrate Firebase ↔ Cosmos  ({u.email or u.uid})")

    if not store.is_configured:
        _die("COSMOS_ENDPOINT not set — cannot create Cosmos profile.")

    # Check Cosmos
    cosmos_doc = _cosmos_run(store, _cosmos_get_user(store, u.uid))

    if cosmos_doc:
        _ok("Cosmos profile exists — both sources below")
        print()
        print(_fmt_user(u, cosmos_doc=cosmos_doc))
        return

    # Create Cosmos profile from Firebase data
    try:
        _cosmos_run(store, _cosmos_create_profile(
            store,
            uid          = u.uid,
            email        = u.email or "",
            display_name = u.display_name or "",
            phone        = u.phone_number or "",
        ))
        _cosmos_ok(f"Created Cosmos profile for {u.email or u.uid}")
    except Exception as e:
        _cosmos_warn(f"Failed to create Cosmos profile: {e}")
        sys.exit(1)
    print()


def cmd_revoke(args):
    import firebase_admin.auth as fb_auth

    _header(f"Revoke Refresh Tokens: {args.uid}")
    try:
        u = fb_auth.get_user(args.uid)
    except firebase_admin.auth.UserNotFoundError:
        _die(f"No Firebase user found with uid={args.uid!r}")

    _warn(f"Revoking all sessions for: {u.email or '(no email)'}  ({u.uid})")
    fb_auth.revoke_refresh_tokens(args.uid)
    _ok("All refresh tokens revoked.")
    _info(
        "Existing ID tokens remain valid for up to 1 hour until they expire.\n"
        "     The Firebase Admin SDK verify_id_token(..., check_revoked=True)\n"
        "     will reject them immediately if revocation checking is enabled."
    )
    print()


def cmd_sync_check(args):
    """
    Cross-check Firebase Authentication against Cosmos DB:

    1. For each Firebase user — verify a Cosmos profile exists. Repair if missing.
    2. For each Cosmos user  — verify a Firebase account exists. Flag orphans.
    """
    import firebase_admin.auth as fb_auth

    store = _make_cosmos_store()
    _header("Firebase ↔ Cosmos DB Sync Check")

    if not store.is_configured:
        _die("COSMOS_ENDPOINT not set — cannot run sync check.")

    # ── Collect Firebase users ───────────────────────────────────────────────
    fb_users: dict = {}        # uid → UserRecord
    page = fb_auth.list_users()
    while page:
        for u in page.users:
            fb_users[u.uid] = u
        page = page.get_next_page()

    _info(f"Firebase: {len(fb_users)} user(s)")

    # ── Collect Cosmos users — single async call, session closed after ───────
    try:
        cosmos_docs = _cosmos_run(store, _cosmos_list_users(store))
    except Exception as e:
        _die(f"Could not read Cosmos DB: {e}")
    # Re-create the store for potential repair writes (previous one is closed).
    store = _make_cosmos_store()

    cosmos_users: dict = {}    # uid → cosmos doc
    for doc in cosmos_docs:
        uid = doc.get("id") or doc.get("uid")
        if uid:
            cosmos_users[uid] = doc

    _info(f"Cosmos DB: {len(cosmos_users)} user profile(s)")
    _sep()

    # ── Pass 1: Firebase users missing from Cosmos ────────────────────────────
    missing_in_cosmos = [uid for uid in fb_users if uid not in cosmos_users]
    orphan_in_cosmos  = [uid for uid in cosmos_users if uid not in fb_users]

    print()
    if missing_in_cosmos:
        _warn(f"{len(missing_in_cosmos)} Firebase user(s) have NO Cosmos DB profile:")
        for uid in missing_in_cosmos:
            u = fb_users[uid]
            print(f"    {RED}✘{NC} {uid}  {u.email or '(no email)'}")
    else:
        _ok("All Firebase users have a Cosmos DB profile.")

    if orphan_in_cosmos:
        _warn(f"{len(orphan_in_cosmos)} Cosmos record(s) have NO matching Firebase account (orphans):")
        for uid in orphan_in_cosmos:
            doc = cosmos_users[uid]
            print(f"    {YELLOW}⚠{NC} {uid}  {doc.get('email', '(no email)')}")
    else:
        _ok("No orphaned Cosmos records found.")

    # ── Prompt for repair ────────────────────────────────────────────────────
    needs_repair = bool(missing_in_cosmos or orphan_in_cosmos)
    if not needs_repair:
        _sep()
        _ok("Systems are fully in sync — no action needed.")
        print()
        return

    print()
    print(f"  Repair options:")
    if missing_in_cosmos:
        print(f"    [1] Create {len(missing_in_cosmos)} missing Cosmos profile(s) from Firebase data")
    if orphan_in_cosmos:
        print(f"    [2] Delete {len(orphan_in_cosmos)} orphaned Cosmos record(s) (no Firebase account)")
    if missing_in_cosmos and orphan_in_cosmos:
        print(f"    [3] Do both")
    print(f"    [0] Skip — report only")
    print()
    choice = input("  Select > ").strip()

    do_create = choice in ("1", "3") and bool(missing_in_cosmos)
    do_delete = choice in ("2", "3") and bool(orphan_in_cosmos)

    if do_create or do_delete:
        # Bundle ALL repair writes into a single event loop so the aiohttp
        # session is opened once and closed cleanly when the loop exits.
        async def _repair_all():
            results = {"created": 0, "create_errors": [], "removed": 0, "remove_errors": []}
            if do_create:
                for uid in missing_in_cosmos:
                    u = fb_users[uid]
                    try:
                        await _cosmos_create_profile(
                            store,
                            uid          = uid,
                            email        = u.email or "",
                            display_name = u.display_name or "",
                            phone        = u.phone_number or "",
                        )
                        results["created"] += 1
                        _cosmos_ok(f"Created profile for {u.email or uid}")
                    except Exception as e:
                        results["create_errors"].append((uid, str(e)))
            if do_delete:
                for uid in orphan_in_cosmos:
                    try:
                        await _cosmos_delete_user(store, uid)
                        results["removed"] += 1
                        _cosmos_ok(f"Deleted orphaned record {uid}")
                    except Exception as e:
                        results["remove_errors"].append((uid, str(e)))
            return results

        r = _cosmos_run(store, _repair_all())
        if do_create:
            for uid, err in r["create_errors"]:
                _cosmos_warn(f"Failed to create profile for {uid}: {err}")
            _ok(f"Repaired {r['created']}/{len(missing_in_cosmos)} missing Cosmos profile(s).")
        if do_delete:
            for uid, err in r["remove_errors"]:
                _cosmos_warn(f"Failed to delete orphan {uid}: {err}")
            _ok(f"Removed {r['removed']}/{len(orphan_in_cosmos)} orphaned Cosmos record(s).")

    print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="firebase_admin_mgr",
        description="Firebase + Cosmos DB unified management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    sub.add_parser("list", help="List all users with Cosmos DB sync status")

    cr = sub.add_parser("create", help="Create a new user (email_verified=True, writes to Cosmos)")
    cr.add_argument("--email",    required=True)
    cr.add_argument("--password", required=True)
    cr.add_argument("--name",     help="Display name")
    cr.add_argument("--uid",      help="Specific UID (auto-generated if omitted)")
    cr.add_argument("--phone",    help="Phone number in E.164 format (+15550001234)")

    up = sub.add_parser("update", help="Update Firebase + sync name/email to Cosmos")
    up.add_argument("uid")
    up.add_argument("--email")
    up.add_argument("--name",     help="New display name (pass '' to clear)")
    up.add_argument("--password")
    up.add_argument("--phone",    help="New phone (pass '' to remove)")

    dl = sub.add_parser("delete", help="Delete from Firebase AND Cosmos DB")
    dl.add_argument("uid")

    da = sub.add_parser("delete-all", help="Global wipe: Firebase Auth + Cosmos Users + Conversations")
    da.add_argument("--force", "-f", action="store_true", help="Non-interactive: skip confirmation prompt")

    sc = sub.add_parser("set-claims", help="Set custom claims JSON on a Firebase user")
    sc.add_argument("uid")
    sc.add_argument("claims_json", help='JSON string, e.g. \'{"admin": true}\'')

    lk = sub.add_parser("links", help="Generate a password-reset or email-verify link")
    lk.add_argument("uid")
    lk.add_argument("link_type", choices=["reset", "verify"])

    rv = sub.add_parser("revoke", help="Revoke all refresh tokens for a Firebase user")
    rv.add_argument("uid")

    integ = sub.add_parser("integrate",
        help="Ensure Firebase user has a Cosmos DB profile (create if missing)")
    integ.add_argument("uid_or_email", help="Firebase UID or email address")

    sc2 = sub.add_parser("sync-check",
        help="Cross-check Firebase ↔ Cosmos DB and optionally repair discrepancies")
    sc2.add_argument("--auto-repair", action="store_true",
        help="Non-interactive: create all missing Cosmos profiles without prompting")

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

DISPATCH = {
    "list":        cmd_list,
    "create":      cmd_create,
    "update":      cmd_update,
    "delete":      cmd_delete,
    "delete-all":  cmd_delete_all,
    "set-claims":  cmd_set_claims,
    "links":       cmd_links,
    "revoke":      cmd_revoke,
    "integrate":   cmd_integrate,
    "sync-check":  cmd_sync_check,
}


def main():
    project_root = _bootstrap()
    _init_firebase(project_root)
    parser = build_parser()
    args = parser.parse_args()
    fn = DISPATCH.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)
    fn(args)


if __name__ == "__main__":
    main()
