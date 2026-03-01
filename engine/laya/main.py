"""Laya Engine — FastAPI entry point."""

from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from laya.agents import session_manager
from laya.api.actions_api import router as actions_router
from laya.api.audit_api import router as audit_router
from laya.api.cards_api import router as cards_router
from laya.api.connections_api import router as connections_router
from laya.api.chat_api import router as chat_router
from laya.api.dashboard_api import router as dashboard_router
from laya.api.diagnostics_api import router as diagnostics_router
from laya.api.events import router as events_router
from laya.api.health import router as health_router
from laya.api.rules_api import router as rules_router
from laya.api.settings_api import router as settings_router
from laya.api.team import router as team_router
from laya.api.websocket import manager
from laya.api.workspace_api import router as workspace_router
from laya.api.ws_router import handle_ws_message
from laya.config import ENGINE_HOST, ENGINE_PORT, ensure_directories, load_repos, load_rules, load_team
from laya.integrations.n8n_bootstrap import ensure_n8n_ready
from laya.db.chromadb_store import connect_chromadb, disconnect_chromadb
from laya.db.migrate import run_migrations
from laya.db.sqlite import connect, disconnect
from laya.logging_setup import setup_logging
from laya.scheduler import start_scheduler, stop_scheduler
from laya.security.keychain import load_all_keys_to_env

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    setup_logging()
    ensure_directories()
    log.info("engine_starting", host=ENGINE_HOST, port=ENGINE_PORT)

    # Connect to SQLite and run migrations
    db = await connect()
    await run_migrations(db)

    # Ensure config files exist (creates defaults on first launch)
    load_team()
    load_rules()
    load_repos()
    log.info("config_loaded")

    # Load API keys from OS keychain into environment
    load_all_keys_to_env()

    # Auto-provision n8n (owner account + API key)
    try:
        n8n_result = await ensure_n8n_ready()
        log.info("n8n_bootstrap", **n8n_result)
    except Exception as e:
        log.warning("n8n_bootstrap_failed", error=str(e))

    # Connect ChromaDB vector store
    connect_chromadb()

    # Start briefing scheduler
    start_scheduler()

    log.info("engine_ready")
    yield

    # Shutdown
    log.info("engine_stopping")
    stop_scheduler()
    await session_manager.cleanup_on_shutdown()
    disconnect_chromadb()
    await disconnect()


app = FastAPI(title="Laya Engine", version="0.1.0", lifespan=lifespan)

# Allow the Tauri/SvelteKit frontend to talk to the engine
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log full request body on validation failure to help debug n8n payload issues."""
    try:
        body = await request.body()
        body_text = body.decode("utf-8", errors="replace")
    except Exception:
        body_text = "<unreadable>"
    log.warning(
        "request_validation_failed",
        path=str(request.url.path),
        errors=exc.errors(),
        body=body_text[:2000],
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return structured JSON error."""
    log.error("unhandled_error", path=str(request.url.path), method=request.method, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": str(exc)},
    )


# Register REST routers
app.include_router(actions_router)
app.include_router(audit_router)
app.include_router(cards_router)
app.include_router(connections_router)
app.include_router(chat_router)
app.include_router(dashboard_router)
app.include_router(diagnostics_router)
app.include_router(events_router)
app.include_router(health_router)
app.include_router(team_router)
app.include_router(rules_router)
app.include_router(settings_router)
app.include_router(workspace_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time UI communication."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            log.debug("ws_message_received", data=data[:200])
            await handle_ws_message(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(
        "laya.main:app",
        host=ENGINE_HOST,
        port=ENGINE_PORT,
        reload=True,
    )
