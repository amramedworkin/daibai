"""
DaiBai API Server

FastAPI backend providing REST and WebSocket endpoints for the GUI.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from ..core.config import load_config, Config
from ..core.agent import DaiBaiAgent


app = FastAPI(title="DaiBai", description="AI Database Assistant API")

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


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


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
from .model_discovery import fetch_provider_models


class FetchModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@app.post("/api/config/fetch-models")
async def fetch_models(request: FetchModelsRequest):
    """Fetch available models from an LLM provider."""
    result = await fetch_provider_models(
        provider=request.provider,
        api_key=request.api_key,
        base_url=request.base_url,
    )
    return result


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
                
                # Execute if requested
                if execute and sql:
                    df = agent.run_sql(sql)
                    if df is not None:
                        await websocket.send_json({
                            "type": "results",
                            "content": df.to_dict(orient="records"),
                            "row_count": len(df),
                            "columns": list(df.columns),
                            "conversation_id": conv_id
                        })
                
                # Add to conversation
                _conversations[conv_id].append({
                    "role": "assistant",
                    "content": sql or "Could not generate SQL",
                    "sql": sql,
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
