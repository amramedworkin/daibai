"""
DaiBai API Server

FastAPI backend providing REST and WebSocket endpoints for the GUI.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage Cosmos DB connection lifecycle. Prevents connection leaks.
    Startup: init CosmosStore and attach to app.state.
    Shutdown: close the store (client + credential) gracefully.
    """
    STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.state.store = CosmosConversationStore()
    yield
    if hasattr(app.state, "store") and app.state.store:
        await app.state.store.close()


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
    name = claims.get("name", "")

    user_record: Dict[str, Any] = {
        "id": uid,
        "uid": uid,
        "username": email,
        "display_name": name,
        "onboarded_at": datetime.now().isoformat(),
    }

    try:
        await store.upsert_user(user_record)
    except Exception as e:
        print(f"[onboard] Cosmos upsert skipped: {e}", flush=True)

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
        print(f"[fetch-models] endpoint: provider={request.provider!r} api_key={'SET' if api_key else 'MISSING'} base_url={base_url!r}", flush=True)
    try:
        result = await fetch_provider_models(
            provider=request.provider,
            api_key=api_key,
            base_url=base_url,
        )
        if _debug:
            print(f"[fetch-models] result: models={len(result.get('models', []))} error={result.get('error')!r}", flush=True)
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
    """Process a natural language query."""
    agent = get_agent()
    conv_id = request.conversation_id or str(uuid.uuid4())

    # Pull: get history at start
    history = await store.get_history(conv_id)

    user_msg = {
        "role": "user",
        "content": request.query,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        # Process: pass history to the LLM
        sql = await agent.generate_sql_async(request.query, "sql", history=history)
        results = None
        row_count = None
        if request.execute and sql:
            df = agent.run_sql(sql)
            if df is not None:
                results = _dataframe_to_json_safe(df)
                row_count = len(df)

        assistant_msg = {
            "role": "assistant",
            "content": sql or "Could not generate SQL",
            "sql": sql,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        # Push: append both messages and upsert
        updated = history + [user_msg, assistant_msg]
        await store.upsert_history(conv_id, updated)

        return QueryResponse(
            sql=sql,
            explanation="Generated SQL query",
            results=results,
            row_count=row_count,
            conversation_id=conv_id,
        )
    except Exception as e:
        error_msg = str(e)
        error_assistant_msg = {
            "role": "assistant",
            "content": f"Error: {error_msg}",
            "timestamp": datetime.now().isoformat(),
        }
        updated = history + [user_msg, error_assistant_msg]
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


# WebSocket for streaming responses
@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for streaming chat responses. Requires Firebase token in query param."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return
    try:
        auth.verify_firebase_token(token)
    except HTTPException:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    agent = get_agent()
    store = websocket.app.state.store

    try:
        while True:
            data = await websocket.receive_json()
            query = data.get("query", "")
            conv_id = data.get("conversation_id", str(uuid.uuid4()))
            execute = data.get("execute", False)

            # Pull: get history at start
            history = await store.get_history(conv_id)

            user_msg = {
                "role": "user",
                "content": query,
                "timestamp": datetime.now().isoformat(),
            }

            # Send acknowledgment
            await websocket.send_json({"type": "ack", "conversation_id": conv_id})

            try:
                # Process: pass history to the LLM
                sql = await agent.generate_sql_async(query, "sql", history=history)
                await websocket.send_json({
                    "type": "sql",
                    "content": sql,
                    "conversation_id": conv_id,
                })

                results = None
                if execute and sql:
                    df = agent.run_sql(sql)
                    if df is not None:
                        results = _dataframe_to_json_safe(df)
                        await websocket.send_json({
                            "type": "results",
                            "content": results,
                            "row_count": len(df),
                            "columns": list(df.columns),
                            "conversation_id": conv_id,
                        })

                assistant_msg = {
                    "role": "assistant",
                    "content": sql or "Could not generate SQL",
                    "sql": sql,
                    "results": results,
                    "timestamp": datetime.now().isoformat(),
                }
                # Push: append both messages and upsert
                updated = history + [user_msg, assistant_msg]
                await store.upsert_history(conv_id, updated)

                await websocket.send_json({"type": "done", "conversation_id": conv_id})

            except Exception as e:
                error_assistant_msg = {
                    "role": "assistant",
                    "content": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
                updated = history + [user_msg, error_assistant_msg]
                await store.upsert_history(conv_id, updated)
                await websocket.send_json({
                    "type": "error",
                    "content": str(e),
                    "conversation_id": conv_id,
                })

    except WebSocketDisconnect:
        pass


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
