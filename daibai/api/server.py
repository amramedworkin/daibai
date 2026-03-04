"""
DaiBai API Server

FastAPI backend providing REST and WebSocket endpoints for the GUI.
"""

import asyncio
import json
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
_LOG_BACKUP_COUNT = 7               # keep 7 days of rotated files


class _SizeAndTimeRotatingHandler(logging.handlers.TimedRotatingFileHandler):
    """Rotate on midnight OR when the file reaches *max_bytes* — whichever is first.

    Python's standard library only offers time-based or size-based rotation
    independently.  This subclass combines both by extending shouldRollover().
    """

    def __init__(self, filename: str, max_bytes: int = _LOG_MAX_BYTES, **kwargs):
        super().__init__(filename, **kwargs)
        self.max_bytes = max_bytes

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self.stream and self.stream.tell() >= self.max_bytes:
            return True
        return super().shouldRollover(record)


def _setup_file_logging() -> Path:
    """Attach a rolling file handler to the root logger.

    Log directory: <project_root>/logs/

    Files rotate at midnight or at 10 MB, whichever comes first.
    Seven days of history are retained, then older files are removed.

    Returns the resolved log file path so it can be included in startup logs.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "daibai.log"

    handler = _SizeAndTimeRotatingHandler(
        filename=str(log_file),
        max_bytes=_LOG_MAX_BYTES,
        when="midnight",              # daily rollover at 00:00 local time
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,                  # create the file immediately on startup
    )
    handler.suffix = "%Y-%m-%d"       # rotated files: daibai.log.2026-02-21
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(logging.INFO)

    # Attach to the root logger — all daibai.* loggers inherit it automatically.
    logging.getLogger().addHandler(handler)
    return log_file


# stdout handler for local development
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

# Suppress Azure SDK HTTP logging — 404s on missing Key Vault secrets are expected.
# Config tries all 10 LLM secret names (OPENAI-API-KEY, GEMINI-API-KEY, etc.); only
# the ones you've stored exist; the rest return 404 and are silently skipped.
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
# Suppress Cosmos SDK request/header logs — clogs logs with no value.
logging.getLogger("azure.cosmos").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

# rolling file handler (project logs/ directory)
_log_file_path = _setup_file_logging()

logger = logging.getLogger("daibai.websocket")

# ── aiohttp session tracing (before Azure SDK imports) ───────────────────────
# Log every ClientSession open/close with context tags to find unclosed sessions.
from .aiohttp_tracing import _patch_aiohttp_session_tracing, tag_aiohttp_session

_patch_aiohttp_session_tracing()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, UploadFile, File, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from ..core.config import load_config, Config
from ..core.agent import DaiBaiAgent
from .database import CosmosConversationStore
from . import auth
from .auth import get_current_user
from ..core import playground_manager
from ..core.playground_manager import (
    get_chinook_schema,
    execute_playground_query,
    reset_playground,
    PlaygroundError,
    QueryTimeoutError,
)


def get_store(request: Request) -> CosmosConversationStore:
    """Get CosmosConversationStore from app state."""
    return request.app.state.store


def _dataframe_to_json_safe(df) -> List[Dict[str, Any]]:
    """Convert DataFrame to list of dicts with Timestamp/datetime/numpy types made JSON-serializable."""
    import pandas as pd
    from datetime import datetime, date
    records = df.to_dict(orient="records")
    out = []
    for row in records:
        new_row = {}
        for k, v in row.items():
            if pd.isna(v):
                new_row[k] = None
            elif isinstance(v, (pd.Timestamp, datetime, date)):
                new_row[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
            elif hasattr(v, "item"):  # numpy scalar (int64, float64, etc.)
                new_row[k] = v.item()
            elif isinstance(v, (bytes, bytearray)):
                new_row[k] = v.decode("utf-8", errors="replace")
            else:
                new_row[k] = v
        out.append(new_row)
    return out


def _playground_rows_to_records(
    columns: list, rows: list
) -> List[Dict[str, Any]]:
    """Convert execute_playground_query output (rows as lists) to list-of-dicts."""
    return [dict(zip(columns, row)) for row in rows]


def _is_anonymous_user(claims: Dict[str, Any]) -> bool:
    """Return True when the Firebase token belongs to an anonymous (signInAnonymously) session.

    get_current_user wraps raw token claims inside {"uid": …, "email": …, "claims": {…}}.
    This helper handles both that wrapper and raw claim dicts.
    """
    raw = claims.get("claims", claims)
    return raw.get("firebase", {}).get("sign_in_provider") == "anonymous"


# Chinook-specific system prompt injected when is_playground=True.
_CHINOOK_SYSTEM_PROMPT = (
    "You are a SQL expert for the Chinook music store sample database (SQLite dialect).\n"
    "The database has 11 tables: Artist, Album, Track, MediaType, Genre, Employee, "
    "Customer, Invoice, InvoiceLine, Playlist, PlaylistTrack.\n"
    "Use only standard SQLite syntax. Do NOT use SHOW TABLES or INFORMATION_SCHEMA.\n"
    "Return ONLY a SQL query inside a ```sql … ``` code block — no prose, no explanation.\n\n"
    "Schema:\n"
)
_CHINOOK_SYSTEM_PROMPT += get_chinook_schema()


_PLAYGROUND_LLM_TIMEOUT: float = 60.0   # seconds before we give up on the LLM


async def _generate_playground_sql(
    agent: DaiBaiAgent,
    user_query: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Use the LLM to generate SQL targeted at the Chinook SQLite playground."""
    enhanced_prompt = (
        "Generate ONLY a SQL query (SELECT by default unless the user explicitly asks "
        "to insert/update/delete) for this request.\n"
        f"Database: Chinook (SQLite)\n\n"
        f"Request: {user_query}\n\n"
        "Return the SQL in a ```sql code block. Do not execute it."
    )
    context: Dict[str, Any] = {
        "system_prompt": _CHINOOK_SYSTEM_PROMPT,
        "schema": get_chinook_schema(),
    }
    if history:
        context["messages"] = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in history
        ]
    try:
        response = await asyncio.wait_for(
            agent.generate_async(enhanced_prompt, context),
            timeout=_PLAYGROUND_LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise PlaygroundError(
            f"SQL generation timed out after {_PLAYGROUND_LLM_TIMEOUT:.0f} s. "
            "Please try a simpler question."
        )
    return response.sql or agent._extract_sql(response.text)


async def _run_playground(
    agent: DaiBaiAgent,
    user_query: str,
    execute: bool,
    history: Optional[List[Dict[str, Any]]] = None,
) -> tuple:
    """
    Generate SQL via the Chinook system prompt, then optionally execute it
    against playground.db.  Returns (sql, results, row_count).
    """
    sql = await _generate_playground_sql(agent, user_query, history)
    results = None
    row_count = None
    if execute and sql:
        # execute_playground_query is synchronous (uses threading.Timer); run in
        # a thread so we don't block the async event loop.
        try:
            raw = await asyncio.to_thread(execute_playground_query, sql)
        except FileNotFoundError:
            # playground.db hasn't been created yet — auto-reset from master.
            logger.warning(
                "playground.db missing — auto-initialising from chinook_master.db",
                extra={"action": "auto_reset_playground"},
            )
            await asyncio.to_thread(reset_playground)
            raw = await asyncio.to_thread(execute_playground_query, sql)
        results = _playground_rows_to_records(raw["columns"], raw["rows"])
        row_count = raw["row_count"]
    return sql, results, row_count


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage Cosmos DB connection lifecycle. Prevents connection leaks.
    Startup: load .env first (so AZURE_* etc. are in os.environ before any SDK use),
    then init CosmosStore and attach to app.state.
    Shutdown: close the store (client + credential) gracefully.
    """
    # Load .env before any Azure SDK access (DefaultAzureCredential reads AZURE_* from os.environ)
    from dotenv import load_dotenv
    _project_root = Path(__file__).resolve().parent.parent.parent
    for loc in [_project_root / ".env", Path.cwd() / ".env", Path.home() / ".daibai" / ".env"]:
        try:
            if loc.exists():
                load_dotenv(loc)
                break
        except OSError:
            pass

    STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    app.state.store = CosmosConversationStore(tag="Cosmos DB Initialization")
    logger.info(
        "DaiBai server started",
        extra={
            "store":    "CosmosConversationStore",
            "log_file": str(_log_file_path),
            "log_max_bytes": _LOG_MAX_BYTES,
            "log_rotate": "midnight + 10 MB",
            "log_retention_days": _LOG_BACKUP_COUNT,
        },
    )
    yield
    if hasattr(app.state, "store") and app.state.store:
        await app.state.store.close()
    logger.info("DaiBai server shut down cleanly")


app = FastAPI(title="DaiBai", description="AI Database Assistant API", lifespan=lifespan)


class COOPMiddleware(BaseHTTPMiddleware):
    """Required for FirebaseUI popup sign-in flows (Google, GitHub) to communicate
    with the opener window without being blocked by cross-origin isolation."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response


app.add_middleware(COOPMiddleware)

# Global state
_agent: Optional[DaiBaiAgent] = None
_config: Optional[Config] = None


def get_agent() -> DaiBaiAgent:
    """Get or create the DaiBai agent."""
    global _agent, _config
    if _agent is None:
        _config = load_config()
        _agent = DaiBaiAgent(config=_config, auto_train=True)
    return _agent


def get_config() -> Config:
    """Get the current config."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


# Pydantic models
class QueryRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    execute: bool = False
    is_playground: bool = False
    verbose: bool = False
    database: Optional[str] = None  # Client's selected DB; syncs agent before processing


class QueryResponse(BaseModel):
    sql: Optional[str]
    explanation: str
    results: Optional[List[Dict[str, Any]]] = None
    row_count: Optional[int] = None
    conversation_id: str


class SettingsResponse(BaseModel):
    databases: List[str]
    llm_providers: List[str]
    llm_provider_configs: Optional[Dict[str, Dict[str, Any]]] = None
    modes: List[str]
    current_database: Optional[str]
    current_llm: Optional[str]
    current_mode: str
    # Index status for current_database — enables auto-index when not_indexed
    is_indexed: Optional[bool] = None
    last_indexed_at: Optional[str] = None


class SettingsUpdate(BaseModel):
    database: Optional[str] = None
    llm: Optional[str] = None
    mode: Optional[str] = None


class ConfigUpdate(BaseModel):
    """Nested config matching daibai.yaml structure for future-proofing."""
    account: Optional[Dict[str, Any]] = None
    llm: Optional[Dict[str, Any]] = None
    llm_providers: Optional[Dict[str, Dict[str, Any]]] = None
    databases: Optional[Dict[str, Any]] = None
    preferences: Optional[Dict[str, Any]] = None
    data_privacy: Optional[Dict[str, Any]] = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    message_count: int


class OnboardRequest(BaseModel):
    uid: Optional[str] = None
    username: Optional[str] = None
    display_name: Optional[str] = None


class ProfilePatchRequest(BaseModel):
    display_name: Optional[str] = None
    phone_number: Optional[str] = None


# Static files path
STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"

@app.get("/api/auth-config", include_in_schema=False)
async def get_auth_config():
    """
    Public endpoint returning auth configuration for the frontend.
    Returns Firebase config vars sourced from environment variables.
    """
    import os
    return {
        # Firebase Authentication (primary)
        "firebase_api_key":            os.environ.get("FIREBASE_API_KEY", ""),
        "firebase_auth_domain":        os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "firebase_project_id":         os.environ.get("FIREBASE_PROJECT_ID", ""),
        "firebase_storage_bucket":     os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "firebase_messaging_sender_id": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "firebase_app_id":             os.environ.get("FIREBASE_APP_ID", ""),
    }


@app.post("/api/auth/onboard")
async def auth_onboard(
    request: Request,
    body: OnboardRequest,
    store: CosmosConversationStore = Depends(get_store),
):
    """
    Called by the frontend after Firebase sign-in.
    Decodes the Firebase ID token (unverified — verification via Firebase Admin SDK
    is a future hardening step), extracts uid + email, and upserts a user record
    into the Cosmos DB Users container.
    """
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[len("Bearer "):].strip() if auth_header.startswith("Bearer ") else ""

    claims: Dict[str, Any] = {}
    if token:
        try:
            claims = auth.verify_firebase_token(token)
            logger.info(
                "[AUTH] onboard: token verified uid=%s email=%s email_verified=%s name=%s body_display_name=%r",
                claims.get("uid"),
                claims.get("email"),
                claims.get("email_verified"),
                claims.get("name"),
                body.display_name,
            )
        except HTTPException as e:
            logger.warning("[AUTH] onboard: token verification failed — %s", e.detail)
            claims = {}

    uid = claims.get("uid") or claims.get("user_id") or claims.get("sub") or body.uid
    if not uid:
        raise HTTPException(status_code=401, detail="Missing user identity")

    email = claims.get("email") or body.username or ""

    # Resolve display name: JWT 'name' claim (set by OAuth providers like Google/GitHub)
    # takes precedence, then the body field (sent by the frontend from user.displayName),
    # then fall back to empty. We never overwrite an existing Cosmos name with "".
    name = (claims.get("name") or body.display_name or "").strip()

    # Fields that are always refreshed on every login.
    fields: Dict[str, Any] = {
        "id":       uid,   # patch_user needs this to locate the document
        "type":     "user",
        "uid":      uid,
        "email":    email,
        "username": email,
    }
    # Only write display_name when we actually have a value — preserves any
    # name the user may have set via Edit Profile on a previous session.
    if name:
        fields["display_name"] = name

    try:
        # patch_user fetches the existing record (or creates a stub), merges
        # fields, and upserts — so profile edits done outside onboarding survive.
        user_record = await store.patch_user(user_id=uid, fields=fields)
        # Stamp onboarded_at only if this is the first time we've seen the user.
        is_new_user = not user_record.get("onboarded_at")
        if is_new_user:
            user_record["onboarded_at"] = datetime.now().isoformat()
            await store.upsert_user(user_record)

        logger.info(
            "[AUTH] login uid=%s email=%s display_name=%s new_user=%s",
            uid, email, user_record.get("display_name") or "(none)", is_new_user,
        )

        # Log default database and index status
        try:
            config = get_config()
            dbs = config.list_databases()
            default_db = config.default_database or (dbs[0] if dbs else None)
            if default_db:
                redis_key = _normalize_db_id_for_redis(default_db)
                status = _get_schema_index_status(redis_key)
                logger.info(
                    "[AUTH] login default_database=%s is_indexed=%s last_indexed_at=%s",
                    default_db,
                    status["is_indexed"],
                    status.get("last_indexed_at") or "(unknown)",
                )
            else:
                logger.info("[AUTH] login default_database=(none configured)")
        except Exception as e:
            logger.warning("[AUTH] login default_database check failed — %s", e)
    except Exception as e:
        logger.warning(
            "[AUTH] onboard: Cosmos upsert failed — %s",
            e,
            extra={"uid": fields.get("uid", "unknown")},
        )
        user_record = fields

    return user_record


@app.patch("/api/profile")
async def patch_profile(
    body: ProfilePatchRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """
    Update mutable profile fields (display_name, phone_number) in Cosmos DB.
    Firebase Auth is updated client-side via the SDK; this endpoint keeps Cosmos
    in sync so the fields are searchable server-side.
    """
    uid = current_user["uid"]
    fields: Dict[str, Any] = {}
    if body.display_name is not None:
        fields["display_name"] = body.display_name.strip()
    if body.phone_number is not None:
        fields["phone_number"] = body.phone_number.strip()
    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    updated = await store.patch_user(uid, fields)
    return updated


@app.get("/api/profile")
async def get_profile(
    current_user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """Return the caller's Cosmos DB user profile (creates a stub for new anonymous users)."""
    uid = current_user["uid"]
    record = await store.get_user(oid=uid)
    if record is None:
        record = {"id": uid, "uid": uid, "playground_count": 0}
    return record


_PLAYGROUND_RESET_TIMEOUT = 15.0  # seconds — file copy is sub-second; this is a safety net

@app.post("/api/playground/reset")
async def playground_reset(_user: Dict[str, Any] = Depends(get_current_user)):
    """Restore playground.db from the read-only chinook_master.db."""
    try:
        path = await asyncio.wait_for(
            asyncio.to_thread(reset_playground),
            timeout=_PLAYGROUND_RESET_TIMEOUT,
        )
        return {"status": "ok", "path": str(path)}
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Reset timed out — file copy took too long.",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
async def health(request: Request):
    """
    Health check. Returns {"status": "ok", "database": "connected"} only if
    Cosmos DB can be successfully pinged.
    """
    store: CosmosConversationStore = request.app.state.store
    if await store.ping():
        return {"status": "ok", "database": "connected"}
    raise HTTPException(status_code=503, detail={"status": "unhealthy", "database": "disconnected"})


# Add this route after your app initialization
@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    """Serve logo as favicon to avoid 404. Browsers request /favicon.ico by default."""
    favicon_path = STATIC_DIR / "logo.png"
    return FileResponse(favicon_path, media_type="image/png")

# API Endpoints (protected)
@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings(_user: Dict[str, Any] = Depends(get_current_user)):
    """Get current settings and available options. Uses config only; does not create the agent."""
    logger.info("[settings] GET before")
    try:
        config = get_config()
        # Avoid get_agent() — it triggers heavy init (MySQL, Redis, embeddings).
        # Use agent's current selection only if already created; else config defaults.
        agent = _agent if _agent is not None else None
        current_db = agent._current_db if agent else config.default_database
        current_llm_val = agent._current_llm if agent else config.default_llm

        databases = config.list_databases()
        llm_providers = config.list_llm_providers()
        llm_configs = config.get_llm_provider_configs_for_ui()

        # Index status for current_database — triggers auto-index when not_indexed
        is_indexed_val = None
        last_indexed_at_val = None
        if current_db:
            try:
                redis_key = _normalize_db_id_for_redis(current_db)
                idx_status = _get_schema_index_status(redis_key)
                is_indexed_val = idx_status["is_indexed"]
                last_indexed_at_val = idx_status.get("last_indexed_at")
            except Exception:
                pass  # leave as None; don't break settings response

        logger.info(
            "[settings] GET after status=ok databases=%s llm_providers=%s current_database=%s current_llm=%s agent_loaded=%s is_indexed=%s",
            databases, llm_providers, current_db, current_llm_val, agent is not None, is_indexed_val,
        )

        return SettingsResponse(
            databases=databases,
            llm_providers=llm_providers,
            llm_provider_configs=llm_configs,
            modes=["sql", "ddl", "crud"],
            current_database=current_db,
            current_llm=current_llm_val,
            current_mode="sql",
            is_indexed=is_indexed_val,
            last_indexed_at=last_indexed_at_val,
        )
    except Exception as exc:
        logger.exception("[settings] GET after status=error %s", exc)
        raise


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate, _user: Dict[str, Any] = Depends(get_current_user)):
    """Update current settings."""
    agent = get_agent()
    
    if settings.database:
        agent.switch_database(settings.database)
    if settings.llm:
        agent.switch_llm(settings.llm)
    
    return {"status": "ok"}


@app.put("/api/config")
async def update_config(config: ConfigUpdate, _user: Dict[str, Any] = Depends(get_current_user)):
    """Update config (nested JSON matching daibai.yaml structure).
    Frontend sends complete object; backend persists when Stripe/user storage is ready."""
    # TODO: Persist full config to per-user storage in Cosmos DB
    return {"status": "ok"}


@app.post("/api/test-llm")
async def test_llm_connection(body: Dict[str, Any] = Body(default={}), _user: Dict[str, Any] = Depends(get_current_user)):
    """Test LLM provider connectivity. Returns success/error."""
    # TODO: Implement actual connectivity test per provider
    return {"success": True, "message": "Connection test not yet implemented"}


# --- Component health checks for Settings > System Health ---

async def _health_check_redis() -> Dict[str, Any]:
    """Test Redis connectivity (cache, schema status, semantic search)."""
    try:
        from ..core.cache import CacheManager
        cache = CacheManager()
        if cache.ping():
            logger.info("[health] redis: OK")
            return {"ok": True, "message": "PING OK"}
        logger.warning("[health] redis: ping failed (no connection string or unreachable)")
        return {"ok": False, "message": "No connection string or ping failed"}
    except Exception as e:
        logger.exception("[health] redis: %s", e)
        return {"ok": False, "message": str(e)}


async def _health_check_llm() -> Dict[str, Any]:
    """Test LLM provider with a trivial generate call."""
    try:
        agent = get_agent()
        response = await asyncio.wait_for(
            agent.generate_async("Say OK", {}),
            timeout=15.0,
        )
        if response and (response.text or response.sql):
            return {"ok": True, "message": "LLM responded"}
        return {"ok": False, "message": "Empty response"}
    except asyncio.TimeoutError:
        return {"ok": False, "message": "Timeout after 15s"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def _health_check_pruning() -> Dict[str, Any]:
    """Test schema pruning engine (Redis + embeddings)."""
    try:
        agent = get_agent()
        db_name = agent._current_db
        if not db_name and agent.config.databases:
            db_name = next(iter(agent.config.databases.keys()), None)
        if not db_name:
            return {"ok": False, "message": "No database selected"}
        sm = agent._get_schema_manager(db_name)
        if not sm:
            return {"ok": False, "message": "SchemaManager unavailable"}
        ddl_list = sm.search_schema_v1(query="tables", schema_name=db_name, limit=1)
        count = len(ddl_list) if ddl_list else 0
        return {"ok": True, "message": f"OK ({count} table(s) in index)" if count else "Reachable (no tables indexed yet)"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def _health_check_database() -> Dict[str, Any]:
    """Test database connection."""
    try:
        agent = get_agent()
        db_name = agent._current_db
        if not db_name and agent.config.databases:
            db_name = next(iter(agent.config.databases.keys()), None)
        if not db_name:
            return {"ok": False, "message": "No database selected"}
        df = agent.run_sql("SELECT 1 AS ok", db_name)
        if df is not None and len(df) > 0:
            return {"ok": True, "message": f"Connected to {db_name}"}
        return {"ok": False, "message": "Query returned empty"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def _health_check_embeddings() -> Dict[str, Any]:
    """Test embedding model (sentence-transformers)."""
    try:
        from ..core.cache import CacheManager
        cache = CacheManager()
        vec = cache.get_embedding("test")
        if vec and len(vec) > 0:
            return {"ok": True, "message": f"OK ({len(vec)}-dim vectors)"}
        return {"ok": False, "message": "Model unavailable or returned empty vector"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


_HEALTH_HANDLERS_ASYNC = {
    "redis": _health_check_redis,
    "llm": _health_check_llm,
    "embeddings": _health_check_embeddings,
    "pruning": _health_check_pruning,
    "database": _health_check_database,
}


async def _health_check_cosmos(store: CosmosConversationStore) -> Dict[str, Any]:
    """Test Cosmos DB (conversation store)."""
    try:
        _ = await store.get_history("health-check-test")
        return {"ok": True, "message": "Connected"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# --- Model discovery (delegated to model_discovery module) ---
from .model_discovery import fetch_provider_models, safe_str, _sanitize_result


class FetchModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


def _resolve_fetch_models_api_key(provider: str, api_key: Optional[str]) -> Optional[str]:
    """Use api_key from request, or resolve from config/env when masked/empty."""
    if api_key and api_key.strip():
        stripped = api_key.strip()
        # Masked placeholder from UI - don't use it
        if stripped not in ("••••••", "••••••••") and not all(c in "•\u2022" for c in stripped):
            return stripped
    # Resolve from config (daibai.yaml + .env)
    config = get_config()
    for name, cfg in config.llm_providers.items():
        if (cfg.provider_type or "").lower() == (provider or "").lower() and cfg.api_key:
            return cfg.api_key
    # Fallback to env vars for common providers
    import os
    env_keys = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    env_var = env_keys.get((provider or "").lower())
    if env_var:
        return os.environ.get(env_var) or None
    return None


@app.post("/api/config/fetch-models")
async def fetch_models(request: FetchModelsRequest, _user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch available models from an LLM provider."""
    import os
    api_key = _resolve_fetch_models_api_key(request.provider, request.api_key)
    base_url = request.base_url if request.base_url else None
    _debug = os.environ.get("DAIBAI_DEBUG_MODELS", "").strip() in ("1", "true", "yes")
    if _debug:
        logger.debug(
            "fetch-models request",
            extra={
                "provider": request.provider,
                "api_key_present": bool(api_key),
                "base_url": base_url,
            },
        )
    try:
        result = await fetch_provider_models(
            provider=request.provider,
            api_key=api_key,
            base_url=base_url,
        )
        if _debug:
            logger.debug(
                "fetch-models result",
                extra={
                    "model_count": len(result.get("models", [])),
                    "error": result.get("error"),
                },
            )
        return _sanitize_result(result)
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        import traceback
        tb = traceback.format_exc()
        return {
            "models": [],
            "error": safe_str(f"Encoding error: {type(e).__name__}. Traceback: {tb}"),
        }
    except Exception as e:
        return {
            "models": [],
            "error": safe_str(str(e)),
        }


@app.get("/api/conversations", response_model=List[ConversationSummary])
async def list_conversations(
    _user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """List all conversations."""
    items = await store.list_conversations()
    return [ConversationSummary(**item) for item in items]


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    _user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """Get a specific conversation. Returns empty messages if not yet created."""
    messages = await store.get_history(conversation_id)
    return {"id": conversation_id, "messages": messages}


@app.post("/api/conversations")
async def create_conversation(_user: Dict[str, Any] = Depends(get_current_user)):
    """Create a new conversation. Document created on first message."""
    conv_id = str(uuid.uuid4())
    return {"id": conv_id}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    _user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """Delete a conversation."""
    await store.delete_conversation(conversation_id)
    return {"status": "ok"}


@app.post("/api/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    _user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """Process a natural language query (production DB or Chinook playground)."""
    agent   = get_agent()
    conv_id = request.conversation_id or str(uuid.uuid4())
    uid     = _user.get("uid", "")

    # Sync agent to client's selected database when provided
    if request.database and request.database in (agent.config.list_databases() or []):
        agent.switch_database(request.database)

    # ── Quota gate: anonymous users are limited to 20 playground queries ───────
    if request.is_playground and _is_anonymous_user(_user):
        try:
            profile = await store.get_user(oid=uid) or {}
            if int(profile.get("playground_count", 0)) >= 20:
                raise HTTPException(status_code=403, detail="QUOTA_EXCEEDED")
        except HTTPException:
            raise
        except Exception:
            pass  # non-fatal — allow request if Cosmos is unreachable

    history = await store.get_history(conv_id)

    user_msg = {
        "role": "user",
        "content": request.query,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        # Index interrogation: answer schema-status questions directly
        if _is_index_interrogation_query(request.query):
            db_id = "chinook_playground" if request.is_playground else get_agent()._current_db
            if db_id:
                status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
                ddl_changed = None
                if status.get("is_indexed") and status.get("ddl_hash"):
                    current_hash = _compute_ddl_hash_for_db(db_id)
                    if current_hash is not None:
                        ddl_changed = current_hash != status["ddl_hash"]
                if status["is_indexed"]:
                    msg = f"The schema was last indexed at {status.get('last_indexed_at', 'unknown')} (UTC)."
                    if ddl_changed is True:
                        msg += " **The DDL has changed since then** — consider re-indexing for accurate semantic search."
                    elif ddl_changed is False:
                        msg += " The DDL has not changed since the last index."
                else:
                    logger.info("[index] artifact: REST query returned 'not indexed' (db=%s, index_interrogation)", db_id)
                    msg = "The database is not yet indexed. It will be indexed automatically when you select it."
                assistant_msg = {
                    "role": "assistant",
                    "content": msg,
                    "sql": None,
                    "timestamp": datetime.now().isoformat(),
                }
                await store.upsert_history(conv_id, history + [user_msg, assistant_msg])
                return QueryResponse(
                    sql=None,
                    explanation=msg,
                    results=None,
                    row_count=None,
                    conversation_id=conv_id,
                )

        if request.is_playground:
            db_id = "chinook_playground"
            status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
            if not status["is_indexed"]:
                logger.info("[index] REST query: forcing index before execution (db=%s, playground)", db_id)
                await _trigger_index_background(db_id)

            # ── Playground path: Chinook system prompt + SQLite execution ──────
            sql, results, row_count = await _run_playground(
                agent, request.query, execute=request.execute, history=history
            )
        else:
            db_id = agent._current_db
            if db_id:
                status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
                if not status["is_indexed"]:
                    logger.info("[index] REST query: forcing index before execution (db=%s, production)", db_id)
                    await _trigger_index_background(db_id)

            # ── Production path: user's configured database ───────────────────
            sql = None
            try:
                sql = await asyncio.wait_for(
                    agent.generate_sql_async(request.query, "sql", history=history),
                    timeout=_PLAYGROUND_LLM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("Production LLM timed out after %.0fs", _PLAYGROUND_LLM_TIMEOUT)
                sql = f"-- Timed out after {_PLAYGROUND_LLM_TIMEOUT:.0f}s. Try a simpler question or check LLM connectivity."
            results   = None
            row_count = None
            if request.execute and sql:
                df = agent.run_sql(sql)
                if df is not None:
                    results   = _dataframe_to_json_safe(df)
                    row_count = len(df)

        assistant_msg = {
            "role": "assistant",
            "content": sql or "Could not generate SQL",
            "sql": sql,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        updated = history + [user_msg, assistant_msg]
        await store.upsert_history(conv_id, updated)

        # Increment playground quota counter *after* a successful response.
        if request.is_playground and uid:
            try:
                profile   = await store.get_user(oid=uid) or {}
                new_count = int(profile.get("playground_count", 0)) + 1
                await store.patch_user(uid, {"playground_count": new_count})
            except Exception as exc:
                logger.warning(
                    "playground_count increment skipped",
                    extra={"error": str(exc), "uid": uid},
                )

        return QueryResponse(
            sql=sql,
            explanation="Chinook playground query" if request.is_playground else "Generated SQL query",
            results=results,
            row_count=row_count,
            conversation_id=conv_id,
        )

    except (PlaygroundError, QueryTimeoutError) as e:
        error_msg = str(e)
        updated = history + [user_msg, {"role": "assistant", "content": f"Playground error: {error_msg}", "timestamp": datetime.now().isoformat()}]
        await store.upsert_history(conv_id, updated)
        raise HTTPException(status_code=422, detail=error_msg)

    except Exception as e:
        error_msg = str(e)
        updated = history + [user_msg, {"role": "assistant", "content": f"Error: {error_msg}", "timestamp": datetime.now().isoformat()}]
        await store.upsert_history(conv_id, updated)
        raise HTTPException(status_code=500, detail=error_msg)


class ExecuteRequest(BaseModel):
    sql: str


@app.post("/api/execute")
async def execute_sql(request: ExecuteRequest, _user: Dict[str, Any] = Depends(get_current_user)):
    """Execute SQL directly."""
    agent = get_agent()
    try:
        df = agent.run_sql(request.sql)
        if df is not None:
            return {
                "results": _dataframe_to_json_safe(df),
                "row_count": len(df),
                "columns": list(df.columns)
            }
        return {"results": [], "row_count": 0, "columns": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- File upload (local storage; future: Azure Blob) ---
UPLOADS_DIR = Path.home() / ".daibai" / "uploads"


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), _user: Dict[str, Any] = Depends(get_current_user)):
    """Store uploaded file locally. Returns file id, name, size for session reference."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = Path(file.filename or "").suffix or ""
    safe_name = f"{file_id}{ext}"
    path = UPLOADS_DIR / safe_name
    size = 0
    try:
        content = await file.read()
        size = len(content)
        path.write_bytes(content)
        return {"id": file_id, "name": file.filename or "file", "size": size}
    except Exception as e:
        if path.exists():
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schema")
async def get_schema(_user: Dict[str, Any] = Depends(get_current_user)):
    """Get the current database schema."""
    agent = get_agent()
    schema = agent.get_schema()
    return {"schema": schema}


@app.get("/api/tables")
async def get_tables(_user: Dict[str, Any] = Depends(get_current_user)):
    """Get list of tables in current database."""
    agent = get_agent()
    try:
        df = agent.run_sql("SHOW TABLES")
        if df is not None:
            return {"tables": df.iloc[:, 0].tolist()}
        return {"tables": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Schema status & real-time indexing progress ────────────────────────────

async def _trigger_index_background(db_id: str) -> None:
    """Start schema indexing in background. Non-blocking; logs errors."""
    redis_key_id = _normalize_db_id_for_redis(db_id)
    logger.info("[index] background: start db_id=%s", db_id)
    try:
        if redis_key_id == "playground":
            root = Path(__file__).resolve().parent.parent.parent
            import sys
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from scripts.index_db import index_playground
            await asyncio.to_thread(index_playground, "playground", force=True)
            logger.info("[index] background: done db_id=%s", db_id)
        else:
            agent = get_agent()
            sm = agent._get_schema_manager(db_id)
            if sm:
                await asyncio.to_thread(sm.index_schema, schema_name=db_id, force=True)
                logger.info("[index] background: done db_id=%s", db_id)
            else:
                logger.warning("[index] background: skipped db_id=%s — no schema manager", db_id)
    except Exception as e:
        logger.warning("[index] background: failed db_id=%s — %s", db_id, e, extra={"db_id": db_id})


def _normalize_db_id_for_redis(db_id: str) -> str:
    """Map frontend db_id to Redis key suffix (e.g. chinook_playground → playground)."""
    if db_id in ("chinook_playground", "playground"):
        return "playground"
    return db_id


def _get_schema_index_status(redis_key_id: str) -> dict:
    """Read schema index status from Redis. redis_key_id is the normalized DB id.
    Uses short timeouts to avoid blocking; on Redis failure, returns is_indexed=True
    so queries can proceed with full schema fallback.
    """
    from ..core.config import get_redis_connection_string
    from ..core.schema import (
        SCHEMA_STATUS_IS_INDEXED,
        SCHEMA_STATUS_LAST_INDEXED_AT,
        SCHEMA_STATUS_DDL_HASH,
    )

    is_indexed = False
    last_indexed_at = None
    ddl_hash = None

    conn_str = get_redis_connection_string()
    if conn_str:
        try:
            import redis
            redis_client = redis.Redis.from_url(
                conn_str,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
            raw = redis_client.get(f"{SCHEMA_STATUS_IS_INDEXED}:{redis_key_id}")
            is_indexed = raw == "1"
            raw_at = redis_client.get(f"{SCHEMA_STATUS_LAST_INDEXED_AT}:{redis_key_id}")
            last_indexed_at = raw_at if raw_at else None
            raw_hash = redis_client.get(f"{SCHEMA_STATUS_DDL_HASH}:{redis_key_id}")
            ddl_hash = raw_hash if raw_hash else None
        except Exception as e:
            logger.warning("Schema index status check failed (Redis timeout/unreachable), allowing query: %s", e)
            is_indexed = True  # Allow query through; agent uses full schema fallback

    if not is_indexed:
        logger.info("[index] status: db=%s not_indexed (last_indexed_at=%s)", redis_key_id, last_indexed_at)

    return {
        "is_indexed": is_indexed,
        "last_indexed_at": last_indexed_at,
        "ddl_hash": ddl_hash,
    }


def _compute_ddl_hash_for_db(db_id: str) -> str | None:
    """Compute current schema DDL hash for change detection. Playground: None (format differs)."""
    import hashlib
    redis_key_id = _normalize_db_id_for_redis(db_id)
    if redis_key_id == "playground":
        return None  # Playground hash requires index_db's SchemaManager; skip for now
    agent = get_agent()
    sm = agent._get_schema_manager(db_id)
    if not sm:
        return None
    try:
        tables_ddl = sm.discover_schema(db_id)
        if not tables_ddl:
            return None
        ddl_str = "\n".join(f"{t}:{ddl}" for t, ddl in sorted(tables_ddl.items()))
        return hashlib.sha256(ddl_str.encode()).hexdigest()
    except Exception:
        return None


def _is_index_interrogation_query(query: str) -> bool:
    """True if the user is asking about schema index status."""
    q = query.strip().lower()
    patterns = [
        "when was the last time you were indexed",
        "when was the schema indexed",
        "when did you last index",
        "when was i indexed",
        "has the ddl changed since you were indexed",
        "has the ddl changed since index",
        "is the database indexed",
        "is the schema indexed",
        "index status",
        "schema index status",
        "when were you indexed",
        "last indexed",
        "indexed when",
    ]
    return any(p in q for p in patterns)


@app.get("/api/schema/status/{db_id}")
async def schema_index_status(
    db_id: str,
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Return the schema-indexing status for a database connection.

    Maps chinook_playground → playground for Redis keys.
    Includes ddl_changed when current schema hash can be computed.
    """
    redis_key_id = _normalize_db_id_for_redis(db_id)
    status = _get_schema_index_status(redis_key_id)
    ddl_changed = None
    if status.get("is_indexed") and status.get("ddl_hash"):
        current_hash = _compute_ddl_hash_for_db(db_id)
        if current_hash is not None:
            ddl_changed = current_hash != status["ddl_hash"]

    return {
        "db_id": db_id,
        "is_indexed": status["is_indexed"],
        "last_indexed_at": status["last_indexed_at"],
        "ddl_changed": ddl_changed,
    }


@app.websocket("/ws/schema-progress")
async def ws_schema_progress(websocket: WebSocket):
    """
    Stream real-time schema-indexing progress.

    Query params:
      token  – Firebase ID token (required)
      db     – database name to index (optional; falls back to agent's current db)

    Emits a sequence of JSON frames:
      { "type": "progress", "pct": 0-100, "status": "Vectorizing: Orders", "eta": 12.3 }
      { "type": "done",     "pct": 100,   "status": "Indexed N tables",    "eta": 0   }
      { "type": "error",    "message": "..." }   (on failure)
    """
    token  = websocket.query_params.get("token", "")
    db_arg = websocket.query_params.get("db", "")

    try:
        auth.verify_firebase_token(token)
    except HTTPException:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    agent     = get_agent()
    raw_db    = db_arg or agent._current_db or ""
    target_db = _normalize_db_id_for_redis(raw_db)
    logger.info("[index] ws_schema_progress: connected db=%s", target_db or "(none)")
    if not raw_db:
        await websocket.send_json({"type": "error", "message": "No database selected"})
        await websocket.close()
        return

    # Playground uses index_db.index_playground (SQLite); others use SchemaManager.
    is_playground = target_db == "playground"
    if is_playground:

        def _run_index_playground() -> int:
            import sys
            root = Path(__file__).resolve().parent.parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from scripts.index_db import index_playground
            return index_playground("playground", force=True)

        await websocket.send_json({"type": "progress", "pct": 0, "status": "Indexing playground schema…", "eta": 15})
        index_task = asyncio.ensure_future(asyncio.to_thread(_run_index_playground))
    else:
        sm = agent._get_schema_manager(target_db)
        if sm is None:
            await websocket.send_json({
                "type": "error",
                "message": f"Schema manager unavailable for '{target_db}' (Redis not configured?)",
            })
            await websocket.close()
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _progress_cb(pct: float, status: str, eta: float) -> None:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "progress", "pct": round(pct, 1), "status": status, "eta": round(eta, 1)}),
                loop,
            )

        index_task = asyncio.ensure_future(
            asyncio.to_thread(sm.index_schema, schema_name=target_db, force=True, progress_cb=_progress_cb)
        )

    try:
        if not is_playground:
            # Drain the progress queue until the indexing task finishes.
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.25)
                    await websocket.send_json(msg)
                except asyncio.TimeoutError:
                    if index_task.done():
                        break
            while not queue.empty():
                await websocket.send_json(queue.get_nowait())
        else:
            await index_task

        if index_task.exception():
            err = str(index_task.exception())
            logger.warning("[index] ws_schema_progress: error db=%s — %s", target_db, err)
            await websocket.send_json({
                "type":    "error",
                "message": err,
            })
        else:
            n = index_task.result()
            logger.info("[index] ws_schema_progress: done db=%s — %d table(s)", target_db, n)
            await websocket.send_json({
                "type":   "done",
                "pct":    100,
                "status": f"Indexed {n} table{'s' if n != 1 else ''}",
                "eta":    0,
            })

    except WebSocketDisconnect:
        index_task.cancel()

    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# WebSocket for streaming responses
@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for streaming chat responses with detailed Azure telemetry."""
    conn_start  = time.perf_counter()
    token       = websocket.query_params.get("token")
    client_host = websocket.client.host if websocket.client else "unknown"

    # Shared context propagated into every log record from this connection.
    # Each key becomes a searchable customDimension in Azure Monitor / KQL.
    log_context: Dict[str, Any] = {
        "client_ip": client_host,
        "endpoint":  "/ws/chat",
    }

    # ── Authentication ────────────────────────────────────────────────────────
    if not token:
        logger.warning("WebSocket connection rejected: missing token", extra=log_context)
        await websocket.close(code=4001)
        return

    try:
        ws_claims = auth.verify_firebase_token(token)
    except HTTPException as exc:
        logger.warning(
            "WebSocket auth failed",
            extra={**log_context, "auth_error": exc.detail},
        )
        await websocket.close(code=4001)
        return

    await websocket.accept()

    ws_uid       = ws_claims.get("uid", "")
    ws_anonymous = _is_anonymous_user(ws_claims)
    agent        = get_agent()
    store        = websocket.app.state.store

    # ── Notify client if current database needs indexing (auto-kickoff with dialog) ──
    config = get_config()
    current_db = agent._current_db if agent else config.default_database
    if current_db and not ws_anonymous:
        try:
            redis_key = _normalize_db_id_for_redis(current_db)
            idx_status = _get_schema_index_status(redis_key)
            if not idx_status["is_indexed"]:
                logger.info("[index] ws connect: db=%s not_indexed — sending init for auto-index", current_db)
                await websocket.send_json({
                    "type": "init",
                    "index_status": {
                        "database": current_db,
                        "is_indexed": False,
                        "last_indexed_at": idx_status.get("last_indexed_at"),
                    },
                })
        except Exception as e:
            logger.warning("[index] ws connect: could not send index_status — %s", e)

    # Enrich context with identity fields; auth_provider surfaces in KQL instantly.
    log_context.update({
        "uid":           ws_uid,
        "is_anonymous":  ws_anonymous,
        "auth_provider": ws_claims.get("firebase", {}).get("sign_in_provider", "unknown"),
    })
    logger.info(
        "WebSocket connection established",
        extra={**log_context, "auth_latency_sec": round(time.perf_counter() - conn_start, 3)},
    )

    try:
        while True:
            # ── Receive message ───────────────────────────────────────────────
            data          = await websocket.receive_json()
            req_start     = time.perf_counter()
            query         = data.get("query", "")
            conv_id       = data.get("conversation_id", str(uuid.uuid4()))
            execute       = data.get("execute", False)
            is_playground = data.get("is_playground", False)
            verbose       = data.get("verbose", False)
            db_from_client = data.get("database") or None

            # Sync agent to client's selected database when provided
            if db_from_client and db_from_client in (agent.config.list_databases() or []):
                agent.switch_database(db_from_client)

            async def _send_debug(msg: str) -> None:
                if verbose:
                    await websocket.send_json({"type": "debug", "content": msg, "conversation_id": conv_id})

            req_context: Dict[str, Any] = {
                **log_context,
                "conversation_id":   conv_id,
                "is_playground":     is_playground,
                "execute_requested": execute,
                "query_length":      len(query),
            }
            logger.info("Received chat query", extra=req_context)
            await _send_debug("1. Received query")

            # ── Quota gate for anonymous playground users ──────────────────
            if is_playground and ws_anonymous:
                try:
                    profile     = await store.get_user(oid=ws_uid) or {}
                    quota_count = int(profile.get("playground_count", 0))
                    if quota_count >= 20:
                        logger.warning(
                            "Playground quota exceeded — request blocked",
                            extra={**req_context, "playground_count": quota_count},
                        )
                        await websocket.send_json({
                            "type":            "error",
                            "content":         "QUOTA_EXCEEDED",
                            "conversation_id": conv_id,
                        })
                        continue
                except Exception as qe:
                    logger.error(
                        f"Quota check failed: {qe}",
                        extra=req_context,
                        exc_info=True,
                    )
            await _send_debug("2. Quota check passed")

            history = await store.get_history(conv_id)
            req_context["history_length"] = len(history)
            await _send_debug(f"3. Loaded history ({len(history)} messages)")

            user_msg = {
                "role":      "user",
                "content":   query,
                "timestamp": datetime.now().isoformat(),
            }
            await websocket.send_json({"type": "ack", "conversation_id": conv_id})
            await _send_debug("4. Sent ack to client")

            try:
                # ── Index interrogation: answer schema-status questions directly ──
                if _is_index_interrogation_query(query):
                    await _send_debug("5. Index interrogation detected — answering directly")
                    db_id = "chinook_playground" if is_playground else agent._current_db
                    if db_id:
                        status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
                        ddl_changed = None
                        if status.get("is_indexed") and status.get("ddl_hash"):
                            current_hash = _compute_ddl_hash_for_db(db_id)
                            if current_hash is not None:
                                ddl_changed = current_hash != status["ddl_hash"]
                        if status["is_indexed"]:
                            msg = f"The schema was last indexed at {status.get('last_indexed_at', 'unknown')} (UTC)."
                            if ddl_changed is True:
                                msg += " **The DDL has changed since then** — consider re-indexing for accurate semantic search."
                            elif ddl_changed is False:
                                msg += " The DDL has not changed since the last index."
                        else:
                            logger.info("[index] artifact: WS chat returned 'not indexed' (db=%s, index_interrogation)", db_id)
                            msg = "The database is not yet indexed. It will be indexed automatically when you select it."
                        await websocket.send_json({
                            "type": "message",
                            "content": msg,
                            "conversation_id": conv_id,
                        })
                        assistant_msg = {
                            "role": "assistant",
                            "content": msg,
                            "sql": None,
                            "results": None,
                            "timestamp": datetime.now().isoformat(),
                        }
                        await store.upsert_history(conv_id, history + [user_msg, assistant_msg])
                        await websocket.send_json({"type": "done", "conversation_id": conv_id})
                        continue

                if is_playground:
                    await _send_debug("5. Playground path: starting")
                    db_id = "chinook_playground"
                    status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
                    if not status["is_indexed"]:
                        logger.info("[index] WS chat: forcing index before execution (db=%s, playground)", db_id)
                        await websocket.send_json({
                            "type": "message",
                            "content": "Indexing database for AI search — one moment…",
                            "conversation_id": conv_id,
                        })
                        await _trigger_index_background(db_id)

                    # ── Playground path ──────────────────────────────────────
                    await _send_debug("6. Playground: generating SQL via LLM...")
                    llm_start               = time.perf_counter()
                    sql, results, row_count = await _run_playground(
                        agent, query, execute=execute, history=history
                    )
                    req_context["llm_latency_sec"]      = round(time.perf_counter() - llm_start, 3)
                    req_context["generated_sql_length"]  = len(sql) if sql else 0
                    await _send_debug(f"7. Playground: SQL generated ({len(sql or '')} chars)")

                    await websocket.send_json({
                        "type":            "sql",
                        "content":         sql,
                        "conversation_id": conv_id,
                        "is_playground":   True,
                    })
                    if execute and sql:
                        await _send_debug("8. Playground: executing SQL...")
                    if results is not None:
                        cols = list(results[0].keys()) if results else []
                        req_context["result_row_count"] = row_count
                        await websocket.send_json({
                            "type":            "results",
                            "content":         results,
                            "row_count":       row_count,
                            "columns":         cols,
                            "conversation_id": conv_id,
                        })

                else:
                    await _send_debug("5. Production path: starting")
                    db_id = agent._current_db
                    if db_id:
                        status = _get_schema_index_status(_normalize_db_id_for_redis(db_id))
                        if not status["is_indexed"]:
                            logger.info("[index] WS chat: forcing index before execution (db=%s, production)", db_id)
                            await websocket.send_json({
                                "type": "message",
                                "content": "Indexing database for AI search — one moment…",
                                "conversation_id": conv_id,
                            })
                            await _trigger_index_background(db_id)

                    # ── Production path ──────────────────────────────────────
                    await _send_debug("6. Production: generating SQL via agent (schema pruning + LLM)...")
                    llm_start = time.perf_counter()
                    try:
                        sql = await asyncio.wait_for(
                            agent.generate_sql_async(query, "sql", history=history),
                            timeout=_PLAYGROUND_LLM_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Production LLM timed out after %.0fs", _PLAYGROUND_LLM_TIMEOUT)
                        sql = f"-- Timed out after {_PLAYGROUND_LLM_TIMEOUT:.0f}s. Try a simpler question or check LLM connectivity."
                    req_context["llm_latency_sec"]     = round(time.perf_counter() - llm_start, 3)
                    req_context["generated_sql_length"] = len(sql) if sql else 0
                    await _send_debug(f"7. Production: SQL generated ({len(sql or '')} chars)")

                    await websocket.send_json({
                        "type":            "sql",
                        "content":         sql,
                        "conversation_id": conv_id,
                    })
                    results   = None
                    row_count = None
                    if execute and sql:
                        await _send_debug("8. Production: executing SQL...")
                        exec_start = time.perf_counter()
                        df         = agent.run_sql(sql)
                        req_context["exec_latency_sec"] = round(time.perf_counter() - exec_start, 3)
                        if df is not None:
                            results   = _dataframe_to_json_safe(df)
                            row_count = len(df)
                            req_context["result_row_count"] = row_count
                            await websocket.send_json({
                                "type":            "results",
                                "content":         results,
                                "row_count":       row_count,
                                "columns":         list(df.columns),
                                "conversation_id": conv_id,
                            })

                await _send_debug("9. Saving conversation to Cosmos...")
                assistant_msg = {
                    "role":      "assistant",
                    "content":   sql or "Could not generate SQL",
                    "sql":       sql,
                    "results":   results,
                    "timestamp": datetime.now().isoformat(),
                }
                await store.upsert_history(conv_id, history + [user_msg, assistant_msg])

                # Increment quota counter after a successful playground response.
                if is_playground and ws_uid:
                    try:
                        profile   = await store.get_user(oid=ws_uid) or {}
                        new_count = int(profile.get("playground_count", 0)) + 1
                        await store.patch_user(ws_uid, {"playground_count": new_count})
                    except Exception as exc:
                        logger.error(
                            f"Failed to increment playground count: {exc}",
                            extra=req_context,
                        )

                req_context["total_latency_sec"] = round(time.perf_counter() - req_start, 3)
                logger.info("Chat query processed successfully", extra=req_context)
                await _send_debug("10. Done")
                await websocket.send_json({"type": "done", "conversation_id": conv_id})

            except Exception as exc:
                req_context["total_latency_sec"] = round(time.perf_counter() - req_start, 3)
                logger.error(
                    f"Error processing query: {exc}",
                    extra=req_context,
                    exc_info=True,   # full traceback forwarded to Application Insights
                )
                err_msg = str(exc)
                await store.upsert_history(conv_id, history + [{
                    "role":      "assistant",
                    "content":   err_msg,
                    "timestamp": datetime.now().isoformat(),
                }])
                await websocket.send_json({
                    "type":            "error",
                    "content":         err_msg,
                    "conversation_id": conv_id,
                })

    except WebSocketDisconnect:
        log_context["session_duration_sec"] = round(time.perf_counter() - conn_start, 3)
        logger.info("WebSocket client disconnected normally", extra=log_context)
    except Exception as exc:
        log_context["session_duration_sec"] = round(time.perf_counter() - conn_start, 3)
        logger.error(
            f"Unexpected WebSocket error: {exc}",
            extra=log_context,
            exc_info=True,
        )


@app.get("/api/health/ping")
async def api_health_ping():
    """Simple connectivity check; no auth required."""
    return {"status": "ok"}


@app.get("/api/health")
async def api_health_check(
    components: Optional[str] = None,
    _user: Dict[str, Any] = Depends(get_current_user),
    store: CosmosConversationStore = Depends(get_store),
):
    """
    Test system components. ?components=redis,llm,pruning,database,cosmos
    Omit to test all. Returns { component: { ok, message } }.
    """
    all_components = list(_HEALTH_HANDLERS_ASYNC.keys()) + ["cosmos"]
    requested = (
        [c.strip().lower() for c in components.split(",") if c.strip()]
        if components
        else all_components
    )
    result = {}
    for comp in requested:
        if comp == "cosmos":
            result[comp] = await _health_check_cosmos(store)
        elif comp in _HEALTH_HANDLERS_ASYNC:
            result[comp] = await _HEALTH_HANDLERS_ASYNC[comp]()
        else:
            result[comp] = {"ok": False, "message": f"Unknown component: {comp}"}
    return result


# Serve static files and index
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main GUI."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>DaiBai GUI</h1><p>Static files not found.</p>")


@app.get("/verify-email", response_class=HTMLResponse, include_in_schema=False)
async def verify_email_page():
    """Serve the custom email verification page (manual button to apply oobCode)."""
    path = STATIC_DIR / "verify-email.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse("<h1>Not found</h1><p>Verify email page not found.</p>", status_code=404)


# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
