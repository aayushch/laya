"""Laya Engine — FastAPI entry point."""

from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from laya.agents import session_manager
from laya.api.events import router as events_router
from laya.api.health import router as health_router
from laya.api.rules_api import router as rules_router
from laya.api.settings_api import router as settings_router
from laya.api.team import router as team_router
from laya.api.websocket import manager
from laya.api.workspace_api import router as workspace_router
from laya.api.ws_router import handle_ws_message
from laya.config import ENGINE_HOST, ENGINE_PORT, ensure_directories, load_repos, load_rules, load_team
from laya.db.chromadb_store import connect_chromadb, disconnect_chromadb
from laya.db.migrate import run_migrations
from laya.db.sqlite import connect, disconnect
from laya.logging_setup import setup_logging
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

    # Connect ChromaDB vector store
    connect_chromadb()

    log.info("engine_ready")
    yield

    # Shutdown
    log.info("engine_stopping")
    await session_manager.cleanup_on_shutdown()
    disconnect_chromadb()
    await disconnect()


app = FastAPI(title="Laya Engine", version="0.1.0", lifespan=lifespan)

# Register REST routers
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
