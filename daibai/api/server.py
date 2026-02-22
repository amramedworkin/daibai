"""
DaiBai API Server

FastAPI backend providing REST and WebSocket endpoints for the GUI.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from ..core.config import load_config, Config
from ..core.agent import DaiBaiAgent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure static dir exists. Shutdown: (none)."""
    STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="DaiBai", description="AI Database Assistant API", lifespan=lifespan)

# Global state
_agent: Optional[DaiBaiAgent] = None
_config: Optional[Config] = None
_conversations: Dict[str, List[Dict[str, Any]]] = {}


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


# Static files path
STATIC_DIR = Path(__file__).parent.parent / "gui" / "static"


# API Endpoints
@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
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
async def update_settings(settings: SettingsUpdate):
    """Update current settings."""
    agent = get_agent()
    
    if settings.database:
        agent.switch_database(settings.database)
    if settings.llm:
        agent.switch_llm(settings.llm)
    
    return {"status": "ok"}


@app.put("/api/config")
async def update_config(config: ConfigUpdate):
    """Update config (nested JSON matching daibai.yaml structure).
    Frontend sends complete object; backend persists when Stripe/user storage is ready."""
    # TODO: Persist to user storage / Stripe when auth is implemented
    return {"status": "ok"}


@app.post("/api/test-llm")
async def test_llm_connection(body: Dict[str, Any] = Body(default={})):
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
async def fetch_models(request: FetchModelsRequest):
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
async def list_conversations():
    """List all conversations."""
    summaries = []
    for conv_id, messages in _conversations.items():
        if messages:
            first_msg = messages[0]
            title = first_msg.get("content", "")[:50] + "..." if len(first_msg.get("content", "")) > 50 else first_msg.get("content", "New conversation")
            summaries.append(ConversationSummary(
                id=conv_id,
                title=title,
                created_at=first_msg.get("timestamp", datetime.now().isoformat()),
                message_count=len(messages)
            ))
    return sorted(summaries, key=lambda x: x.created_at, reverse=True)


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get a specific conversation."""
    if conversation_id not in _conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conversation_id, "messages": _conversations[conversation_id]}


@app.post("/api/conversations")
async def create_conversation():
    """Create a new conversation."""
    conv_id = str(uuid.uuid4())
    _conversations[conv_id] = []
    return {"id": conv_id}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    if conversation_id in _conversations:
        del _conversations[conversation_id]
    return {"status": "ok"}


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Process a natural language query."""
    agent = get_agent()
    
    # Create or get conversation
    conv_id = request.conversation_id or str(uuid.uuid4())
    if conv_id not in _conversations:
        _conversations[conv_id] = []
    
    # Add user message
    _conversations[conv_id].append({
        "role": "user",
        "content": request.query,
        "timestamp": datetime.now().isoformat()
    })
    
    # Generate SQL
    try:
        sql = await agent.generate_sql_async(request.query, "sql")
        
        results = None
        row_count = None
        
        if request.execute and sql:
            df = agent.run_sql(sql)
            if df is not None:
                results = df.to_dict(orient="records")
                row_count = len(df)
        
        # Add assistant message
        _conversations[conv_id].append({
            "role": "assistant",
            "content": sql or "Could not generate SQL",
            "sql": sql,
            "results": results,
            "timestamp": datetime.now().isoformat()
        })
        
        return QueryResponse(
            sql=sql,
            explanation="Generated SQL query",
            results=results,
            row_count=row_count,
            conversation_id=conv_id
        )
    except Exception as e:
        error_msg = str(e)
        _conversations[conv_id].append({
            "role": "assistant",
            "content": f"Error: {error_msg}",
            "timestamp": datetime.now().isoformat()
        })
        raise HTTPException(status_code=500, detail=error_msg)


class ExecuteRequest(BaseModel):
    sql: str


@app.post("/api/execute")
async def execute_sql(request: ExecuteRequest):
    """Execute SQL directly."""
    agent = get_agent()
    try:
        df = agent.run_sql(request.sql)
        if df is not None:
            return {
                "results": df.to_dict(orient="records"),
                "row_count": len(df),
                "columns": list(df.columns)
            }
        return {"results": [], "row_count": 0, "columns": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- File upload (local storage; future: Azure Blob) ---
UPLOADS_DIR = Path.home() / ".daibai" / "uploads"


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
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
async def get_schema():
    """Get the current database schema."""
    agent = get_agent()
    schema = agent.get_schema()
    return {"schema": schema}


@app.get("/api/tables")
async def get_tables():
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
    """WebSocket endpoint for streaming chat responses."""
    await websocket.accept()
    agent = get_agent()
    
    try:
        while True:
            data = await websocket.receive_json()
            query = data.get("query", "")
            conv_id = data.get("conversation_id", str(uuid.uuid4()))
            execute = data.get("execute", False)
            
            if conv_id not in _conversations:
                _conversations[conv_id] = []
            
            # Add user message
            _conversations[conv_id].append({
                "role": "user",
                "content": query,
                "timestamp": datetime.now().isoformat()
            })
            
            # Send acknowledgment
            await websocket.send_json({
                "type": "ack",
                "conversation_id": conv_id
            })
            
            try:
                # Generate SQL (streaming would go here for supported providers)
                sql = await agent.generate_sql_async(query, "sql")
                
                # Send SQL result
                await websocket.send_json({
                    "type": "sql",
                    "content": sql,
                    "conversation_id": conv_id
                })
                
                results = None
                if execute and sql:
                    df = agent.run_sql(sql)
                    if df is not None:
                        results = df.to_dict(orient="records")
                        await websocket.send_json({
                            "type": "results",
                            "content": results,
                            "row_count": len(df),
                            "columns": list(df.columns),
                            "conversation_id": conv_id
                        })
                
                # Add to conversation (include results for session history)
                _conversations[conv_id].append({
                    "role": "assistant",
                    "content": sql or "Could not generate SQL",
                    "sql": sql,
                    "results": results,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Send completion
                await websocket.send_json({
                    "type": "done",
                    "conversation_id": conv_id
                })
                
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "content": str(e),
                    "conversation_id": conv_id
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
