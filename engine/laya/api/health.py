"""GET /health — System health check endpoint."""

import time

import httpx
import structlog
from fastapi import APIRouter

from laya.config import N8N_URL
from laya.db.chromadb_store import is_chromadb_healthy
from laya.db.sqlite import is_healthy as sqlite_healthy

log = structlog.get_logger()
router = APIRouter()

_start_time = time.time()


@router.get("/health")
async def health_check() -> dict:
    """Check engine, SQLite, and n8n health status."""
    # SQLite
    sqlite_status = "healthy" if await sqlite_healthy() else "unhealthy"

    # n8n
    n8n_status = "unhealthy"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{N8N_URL}/healthz")
            if resp.status_code == 200:
                n8n_status = "healthy"
    except Exception:
        n8n_status = "unreachable"

    # ChromaDB
    chromadb_status = "healthy" if is_chromadb_healthy() else "unhealthy"

    uptime = int(time.time() - _start_time)

    return {
        "engine": "healthy",
        "sqlite": sqlite_status,
        "chromadb": chromadb_status,
        "n8n": n8n_status,
        "uptime_seconds": uptime,
    }
