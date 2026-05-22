# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Laya Engine — FastAPI entry point."""

import asyncio
import os
import signal
import socket
import sys
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
from laya.api.budget_api import router as budget_router
from laya.api.cards_api import router as cards_router
from laya.api.classification_api import router as classification_router
from laya.api.connections_api import router as connections_router
from laya.api.egress_api import router as egress_router
from laya.api.chat_api import router as chat_router
from laya.api.dashboard_api import router as dashboard_router
from laya.api.diagnostics_api import router as diagnostics_router
from laya.api.events import router as events_router
from laya.api.ingestion_errors import router as ingestion_errors_router
from laya.api.mcp_api import (
    ensure_startup_token as ensure_mcp_startup_token,
    register_mcp_transport,
    router as mcp_router,
)
from laya.api.metadata_api import router as metadata_router
from laya.api.omni_api import router as omni_router
from laya.api.health import router as health_router
from laya.api.processing_rules_api import router as processing_rules_router
from laya.api.rules_api import router as rules_router
from laya.api.settings_api import router as settings_router
from laya.api.spaces_api import router as spaces_router
from laya.api.tags_api import router as tags_router
from laya.api.team import router as team_router
from laya.api.trace_api import router as trace_router
from laya.api.websocket import manager
from laya.api.workspace_api import router as workspace_router
from laya.api.ws_router import handle_ws_message
from laya.config import ENGINE_HOST, ENGINE_PORT, ensure_directories, load_repos, load_rules, load_settings, load_team
from laya.http_client import close_client as close_http_client
from laya.integrations.n8n_bootstrap import ensure_n8n_ready, sync_workflows_background
from laya.db.chromadb_store import connect_chromadb, disconnect_chromadb
from laya.db.migrate import run_migrations
from laya.db.sqlite import connect, disconnect
from laya.logging_setup import setup_logging
from laya.pipeline.omni import start_omni_processor, stop_omni_processor
from laya.pipeline.queue import recover_stalled_cards, recover_stalled_events, start_consumer, stop_consumer
from laya.scheduler import start_scheduler, stop_scheduler
from laya.security.keychain import load_all_keys_to_env

log = structlog.get_logger()


def _start_parent_watchdog() -> None:
    """Monitor parent process and shut down if it disappears.

    When Laya.app quits (or crashes), the engine becomes an orphan — its
    parent PID changes to 1 (launchd on macOS).  This watchdog thread polls
    every 2 seconds and sends SIGTERM to ourselves when that happens, giving
    uvicorn a chance to run the lifespan shutdown cleanly.

    On Windows `os.getppid()` is not reliably updated on reparenting, so we
    use psutil.pid_exists() against the original parent PID instead.
    """
    import threading
    import time

    parent_pid = os.getppid()
    if parent_pid <= 1:
        # Already orphaned (launched standalone) — nothing to watch
        return

    if sys.platform == "win32":
        # Prefer LAYA_PARENT_PID (set by the Rust launcher). On Windows, uvicorn
        # re-execs to a worker subprocess, so os.getppid() points at the worker
        # spawner rather than laya-app.exe — the actual process we want to track.
        parent_pid_env = os.environ.get("LAYA_PARENT_PID")
        if parent_pid_env:
            try:
                parent_pid = int(parent_pid_env)
            except ValueError:
                pass
        try:
            import psutil
        except ImportError:
            log.warning("parent_watchdog_unavailable_no_psutil")
            return

        def _watch_win():
            while True:
                time.sleep(2)
                try:
                    if not psutil.pid_exists(parent_pid):
                        log.info("parent_exited", original_parent=parent_pid)
                        os.kill(os.getpid(), signal.SIGTERM)
                        return
                except Exception:
                    pass

        t = threading.Thread(target=_watch_win, daemon=True, name="parent-watchdog")
        t.start()
        log.info("parent_watchdog_started", parent_pid=parent_pid, mode="psutil")
        return

    def _watch():
        while True:
            time.sleep(2)
            current_parent = os.getppid()
            if current_parent != parent_pid:
                log.info(
                    "parent_exited",
                    original_parent=parent_pid,
                    new_parent=current_parent,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    t = threading.Thread(target=_watch, daemon=True, name="parent-watchdog")
    t.start()
    log.info("parent_watchdog_started", parent_pid=parent_pid)


def _kill_stale_engine(host: str, port: int) -> None:
    """If another process is holding our port, kill it before we proceed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.close()
        return  # Port is free
    except OSError:
        sock.close()

    log.warning("port_in_use", host=host, port=port)

    # Find the PID holding the port and kill it. lsof is Unix-only; Windows
    # uses psutil.net_connections() for the same result.
    if sys.platform == "win32":
        try:
            import psutil
            my_pid = os.getpid()
            for conn in psutil.net_connections(kind="tcp"):
                if (
                    conn.laddr
                    and conn.laddr.port == port
                    and conn.status == psutil.CONN_LISTEN
                    and conn.pid
                    and conn.pid != my_pid
                ):
                    log.warning("killing_stale_engine", pid=conn.pid, port=port)
                    try:
                        psutil.Process(conn.pid).terminate()
                    except Exception:
                        pass
            import time

            time.sleep(1)
        except Exception as e:
            log.warning("stale_engine_cleanup_failed", error=str(e))
        return

    import subprocess

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-s", "tcp:listen"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
        my_pid = os.getpid()
        for pid in pids:
            if pid != my_pid:
                log.warning("killing_stale_engine", pid=pid, port=port)
                os.kill(pid, signal.SIGTERM)
        if pids:
            import time

            time.sleep(1)
    except Exception as e:
        log.warning("stale_engine_cleanup_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    setup_logging()
    _start_parent_watchdog()
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

    # Ensure an MCP bearer token exists when auth_mode=bearer so the SSE
    # endpoint is immediately usable. Token only generated if missing.
    ensure_mcp_startup_token()

    # Load custom prompt overrides from ~/.laya/prompts/
    from laya.llm.prompts.overrides import load_custom_prompts
    load_custom_prompts()

    # Auto-detect agent binary paths (if not already configured)
    try:
        from laya.config import detect_agent_paths, save_settings
        _settings = load_settings()
        _agent_paths = _settings.get("agent_paths", {})
        if not any(_agent_paths.values()):
            detected = detect_agent_paths()
            if any(detected.values()):
                _settings["agent_paths"] = detected
                save_settings(_settings)
                log.info("agent_paths_detected", **detected)
    except Exception as e:
        log.warning("agent_path_detection_failed", error=str(e))

    # Auto-provision n8n (owner account + API key)
    try:
        n8n_result = await ensure_n8n_ready()
        log.info("n8n_bootstrap", **n8n_result)
    except Exception as e:
        log.warning("n8n_bootstrap_failed", error=str(e))

    # Import any new bundled workflows in the background — retries for up to
    # 10 min so a slow-starting n8n is handled gracefully.
    from laya.tasks import create_task as create_tracked_task
    _workflow_sync_task = create_tracked_task(sync_workflows_background())  # noqa: F841

    # Connect ChromaDB vector store
    connect_chromadb()

    # Start briefing scheduler
    start_scheduler()

    # Startup budget month-rollover check (fallback for missed rollovers)
    try:
        from laya.pipeline.budget import on_month_rollover, _current_year_month_local, _user_timezone
        tz = _user_timezone()
        current_month = _current_year_month_local(tz)
        # Check if previous month's costs have been snapshotted
        async with db.execute(
            "SELECT year_month FROM monthly_costs ORDER BY year_month DESC LIMIT 1"
        ) as cursor:
            last_snapshot = await cursor.fetchone()
        if last_snapshot and last_snapshot["year_month"] < current_month:
            pass  # snapshot_month is idempotent, safe to call
        elif not last_snapshot:
            pass  # No history yet, nothing to roll over

        # Check if budget pause should be cleared (new month)
        async with db.execute("SELECT paused_at FROM budget_config WHERE id = 1") as cursor:
            cfg = await cursor.fetchone()
        if cfg and cfg["paused_at"]:
            paused_month = cfg["paused_at"][:7]  # 'YYYY-MM' from timestamp
            if paused_month < current_month:
                log.info("startup_budget_rollover", paused_month=paused_month, current_month=current_month)
                await on_month_rollover(paused_month)
    except Exception as e:
        log.warning("startup_budget_check_failed", error=str(e))

    # Recover events and cards orphaned by previous crash/shutdown, then start consumer
    await recover_stalled_events()
    await recover_stalled_cards()
    start_consumer()

    # Start Omni queue processor — picks up any cards left in omni_queue
    # from a previous crash, plus handles all future incremental updates.
    start_omni_processor()

    # Start egress connection health monitor
    from laya.egress.health import start_health_monitor, stop_health_monitor
    await start_health_monitor()

    # Sweep stale agent-upload staging files once at startup, then run an
    # hourly periodic sweep. A startup-only sweep is insufficient on macOS,
    # where the app can stay open for days.
    from laya.agents.staging_cleanup import (
        start_staging_sweeper,
        stop_staging_sweeper,
        sweep_agent_staging,
    )
    try:
        sweep_agent_staging()
    except Exception as e:
        log.warning("agent_staging_sweep_startup_error", error=str(e))
    start_staging_sweeper()

    log.info("engine_ready")
    yield

    # Shutdown
    log.info("engine_stopping")
    await stop_staging_sweeper()
    await stop_health_monitor()
    stop_omni_processor()
    await stop_consumer()
    stop_scheduler()
    await session_manager.cleanup_on_shutdown()

    # Close the HTTP client before cancelling background tasks, while the
    # event loop is still healthy. Closing after aggressive task cancellation
    # caused CancelledError inside httpx's aclose() → anyio sleep(0).
    try:
        await close_http_client()
    except Exception as e:
        log.warning("http_client_close_error", error=str(e))

    # Cancel fire-and-forget application tasks (summarizer, omni, agents,
    # chat, budget checks, etc.) so their in-flight LLM HTTP requests are
    # aborted.  Only tasks created via laya.tasks.create_task are cancelled —
    # uvicorn/starlette internals are left alone so the ASGI shutdown
    # handshake completes without CancelledError tracebacks.
    from laya.tasks import cancel_all as cancel_tracked_tasks
    await cancel_tracked_tasks()

    disconnect_chromadb()
    await disconnect()


app = FastAPI(title="Laya Engine", version="0.1.0", lifespan=lifespan)

# Allow the Tauri/SvelteKit frontend to talk to the engine
app.add_middleware(
    CORSMiddleware,
    # Windows Tauri v2 serves the bundled frontend from http(s)://tauri.localhost/;
    # macOS/Linux use tauri://localhost. Both origins must be allowed so fetch()
    # from the webview (e.g. health polling) isn't blocked by the browser.
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
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
app.include_router(budget_router)
app.include_router(cards_router)
app.include_router(classification_router)
app.include_router(connections_router)
app.include_router(egress_router)
app.include_router(chat_router)
app.include_router(dashboard_router)
app.include_router(diagnostics_router)
app.include_router(events_router)
app.include_router(health_router)
app.include_router(ingestion_errors_router)
app.include_router(mcp_router)
app.include_router(metadata_router)
app.include_router(omni_router)
app.include_router(team_router)
app.include_router(processing_rules_router)
app.include_router(rules_router)
app.include_router(settings_router)
app.include_router(spaces_router)
app.include_router(tags_router)
app.include_router(trace_router)
app.include_router(workspace_router)
register_mcp_transport(app)


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
    # Enable reload only when running from the repo checkout (dev mode).
    # The Tauri-bundled app runs Python source directly (not PyInstaller),
    # so sys.frozen is False — detect dev mode by checking for the repo's
    # engine/.venv directory instead.
    is_dev = (
        not getattr(sys, "frozen", False)
        and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", ".venv"))
    )

    # Kill any stale engine holding our port before uvicorn tries to bind
    _kill_stale_engine(ENGINE_HOST, ENGINE_PORT)

    if is_dev:
        uvicorn.run(
            "laya.main:app",
            host=ENGINE_HOST,
            port=ENGINE_PORT,
            reload=True,
        )
    else:
        uvicorn.run(app, host=ENGINE_HOST, port=ENGINE_PORT)
