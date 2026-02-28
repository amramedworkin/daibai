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

    Log directory follows the XDG Base Directory Specification:
        $XDG_STATE_HOME/daibai/logs/   (default: ~/.local/state/daibai/logs/)

    Files rotate at midnight or at 10 MB, whichever comes first.
    Seven days of history are retained, then older files are removed.

    Returns the resolved log file path so it can be included in startup logs.
    """
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    log_dir = xdg_state / "daibai" / "logs"
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

# rolling file handler (XDG path, size + time rotation)
_log_file_path = _setup_file_logging()

logger = logging.getLogger("daibai.websocket")

# ── OTel Telemetry Router ─────────────────────────────────────────────────────
# Priority:
#   1. APPLICATIONINSIGHTS_CONNECTION_STRING → Azure Monitor (production)
#   2. OTEL_EXPORTER_OTLP_ENDPOINT           → Aspire Dashboard (local dev)
#   3. Neither set                            → stdout + rolling file only
#
# All branches are gracefully skipped if the optional packages are absent so the
# server always boots regardless of which observability extras are installed.
_APPINSIGHTS_CONN = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
_OTLP_ENDPOINT    = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

if _APPINSIGHTS_CONN:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor()
        logger.info(
            "Azure Monitor configured — telemetry forwarded to Application Insights",
            extra={"connection_string_present": True},
        )
    except ImportError:
        logger.warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING is set but "
            "azure-monitor-opentelemetry is not installed. "
            "Run: pip install -e '.[observability]'"
        )

elif _OTLP_ENDPOINT:
    try:
        from opentelemetry.sdk._logs import LoggerProvider, set_logger_provider  # type: ignore[import]
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor       # type: ignore[import]
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter  # type: ignore[import]
        from opentelemetry.sdk._logs.logging import LoggingHandler               # type: ignore[import]

        _logger_provider = LoggerProvider()
        set_logger_provider(_logger_provider)

        # insecure=True is required for plain HTTP/2 on localhost:4317
        _otlp_exporter = OTLPLogExporter(insecure=True)
        _logger_provider.add_log_record_processor(BatchLogRecordProcessor(_otlp_exporter))

        # Bridge: standard Python logging calls → OTel → Aspire Dashboard
        logging.getLogger().addHandler(LoggingHandler(logger_provider=_logger_provider))

        logger.info(
            "OTLP exporter configured — telemetry forwarded to Aspire Dashboard",
            extra={"otlp_endpoint": _OTLP_ENDPOINT},
        )
    except ImportError:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp "
            "is not installed. Run: pip install opentelemetry-exporter-otlp"
        )

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
    Startup: init CosmosStore and attach to app.state.
    Shutdown: close the store (client + credential) gracefully.
    """
    STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Instrument FastAPI with OpenTelemetry so every HTTP request and
    # WebSocket connection is automatically traced.  No-op when the
    # package is absent (local dev without observability deps).
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OpenTelemetry instrumentation enabled")
    except ImportError:
        pass  # observability package not installed — skip silently

    app.state.store = CosmosConversationStore()
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
        except HTTPException:
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
        if not user_record.get("onboarded_at"):
            user_record["onboarded_at"] = datetime.now().isoformat()
            await store.upsert_user(user_record)
    except Exception as e:
        logger.warning(
            "Cosmos upsert skipped during onboarding",
            extra={"error": str(e), "uid": fields.get("uid", "unknown")},
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
    """Get current settings and available options."""
    config = get_config()
    agent = get_agent()
    
    return SettingsResponse(
        databases=config.list_databases(),
        llm_providers=config.list_llm_providers(),
        llm_provider_configs=config.get_llm_provider_configs_for_ui(),
        modes=["sql", "ddl", "crud"],
        current_database=agent.current_database,
        current_llm=agent.current_llm,
        current_mode="sql"
    )


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
        if request.is_playground:
            # ── Playground path: Chinook system prompt + SQLite execution ──────
            sql, results, row_count = await _run_playground(
                agent, request.query, execute=request.execute, history=history
            )
        else:
            # ── Production path: user's configured database ───────────────────
            sql = await agent.generate_sql_async(request.query, "sql", history=history)
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

@app.get("/api/schema/status/{db_id}")
async def schema_index_status(
    db_id: str,
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Return the schema-indexing status for a database connection.

    Reads two Redis keys written by SchemaManager.index_schema():
      schema:status:is_indexed:{db_id}      → "1" when indexed, absent/other = false
      schema:status:last_indexed_at:{db_id} → ISO-8601 UTC timestamp of last run
    """
    from ..core.cache import CacheManager
    from ..core.schema import SCHEMA_STATUS_IS_INDEXED, SCHEMA_STATUS_LAST_INDEXED_AT

    cache = CacheManager()
    redis = cache._get_client()
    is_indexed     = False
    last_indexed_at = None

    if redis:
        try:
            raw = redis.get(f"{SCHEMA_STATUS_IS_INDEXED}:{db_id}")
            is_indexed = raw == "1"
            raw_at = redis.get(f"{SCHEMA_STATUS_LAST_INDEXED_AT}:{db_id}")
            last_indexed_at = raw_at or None
        except Exception:
            pass

    return {
        "db_id":           db_id,
        "is_indexed":      is_indexed,
        "last_indexed_at": last_indexed_at,
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
    target_db = db_arg or agent._current_db

    if not target_db:
        await websocket.send_json({"type": "error", "message": "No database selected"})
        await websocket.close()
        return

    sm = agent._get_schema_manager(target_db)
    if sm is None:
        await websocket.send_json({
            "type": "error",
            "message": f"Schema manager unavailable for '{target_db}' (Redis not configured?)",
        })
        await websocket.close()
        return

    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    queue: asyncio.Queue            = asyncio.Queue()

    def _progress_cb(pct: float, status: str, eta: float) -> None:
        """Called from the worker thread; routes updates into the async queue."""
        asyncio.run_coroutine_threadsafe(
            queue.put({
                "type":   "progress",
                "pct":    round(pct, 1),
                "status": status,
                "eta":    round(eta, 1),
            }),
            loop,
        )

    # Run the blocking index_schema inside a thread pool so the event loop stays free.
    index_task: asyncio.Future = asyncio.ensure_future(
        asyncio.to_thread(sm.index_schema, schema_name=target_db, force=True, progress_cb=_progress_cb)
    )

    try:
        # Drain the queue until the indexing task finishes.
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.25)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                if index_task.done():
                    break

        # Flush any messages that arrived between the last poll and task completion.
        while not queue.empty():
            await websocket.send_json(queue.get_nowait())

        if index_task.exception():
            await websocket.send_json({
                "type":    "error",
                "message": str(index_task.exception()),
            })
        else:
            n = index_task.result()
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

            req_context: Dict[str, Any] = {
                **log_context,
                "conversation_id":   conv_id,
                "is_playground":     is_playground,
                "execute_requested": execute,
                "query_length":      len(query),
            }
            logger.info("Received chat query", extra=req_context)

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

            history = await store.get_history(conv_id)
            req_context["history_length"] = len(history)

            user_msg = {
                "role":      "user",
                "content":   query,
                "timestamp": datetime.now().isoformat(),
            }
            await websocket.send_json({"type": "ack", "conversation_id": conv_id})

            try:
                if is_playground:
                    # ── Playground path ──────────────────────────────────────
                    llm_start               = time.perf_counter()
                    sql, results, row_count = await _run_playground(
                        agent, query, execute=execute, history=history
                    )
                    req_context["llm_latency_sec"]      = round(time.perf_counter() - llm_start, 3)
                    req_context["generated_sql_length"]  = len(sql) if sql else 0

                    await websocket.send_json({
                        "type":            "sql",
                        "content":         sql,
                        "conversation_id": conv_id,
                        "is_playground":   True,
                    })
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
                    # ── Production path ──────────────────────────────────────
                    llm_start = time.perf_counter()
                    sql       = await agent.generate_sql_async(query, "sql", history=history)
                    req_context["llm_latency_sec"]     = round(time.perf_counter() - llm_start, 3)
                    req_context["generated_sql_length"] = len(sql) if sql else 0

                    await websocket.send_json({
                        "type":            "sql",
                        "content":         sql,
                        "conversation_id": conv_id,
                    })
                    results   = None
                    row_count = None
                    if execute and sql:
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


# Serve static files and index
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main GUI."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>DaiBai GUI</h1><p>Static files not found.</p>")


# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
