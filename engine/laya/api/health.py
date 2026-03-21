"""GET /health — System health check endpoint."""

import time

import structlog
from fastapi import APIRouter

from laya.config import get_n8n_config
from laya.db.chromadb_store import is_chromadb_healthy, get_embedding_info
from laya.db.sqlite import is_healthy as sqlite_healthy
from laya.http_client import get_client

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
        n8n_base = get_n8n_config()["base_url"].rstrip("/")
        resp = await get_client().get(f"{n8n_base}/healthz", timeout=2.0)
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
        "embeddings": get_embedding_info(),
    }
