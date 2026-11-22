# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""GET /health — System health check endpoint."""

import time

import structlog
from fastapi import APIRouter, Request

from laya.config import get_n8n_config
from laya.db.chromadb_store import get_chromadb_status, get_embedding_info
from laya.db.sqlite import is_healthy as sqlite_healthy
from laya.http_client import get_client
from laya.security.engine_auth import bearer_matches

log = structlog.get_logger()
router = APIRouter()

_start_time = time.time()


def _token_status(request: Request) -> str:
    """Whether this engine accepts the app token the caller sent.

    /health needs no credential and never refuses a caller. A caller that sends
    its bearer token learns whether this engine holds the same token. After the
    app restarts, the engine from the previous launch can still answer on the
    port for a few seconds, and it holds the previous launch's token. The UI
    finishes its startup only once the answer is "accepted", so it never loads
    its data from that engine.
    """
    if not request.headers.get("authorization"):
        return "absent"
    return "accepted" if bearer_matches(request) else "rejected"


@router.get("/health")
async def health_check(request: Request) -> dict:
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

    # ChromaDB ("starting" while the background connect is still running)
    chromadb_status = get_chromadb_status()

    uptime = int(time.time() - _start_time)

    return {
        "engine": "healthy",
        "sqlite": sqlite_status,
        "chromadb": chromadb_status,
        "n8n": n8n_status,
        "uptime_seconds": uptime,
        "embeddings": get_embedding_info(),
        "token": _token_status(request),
    }
