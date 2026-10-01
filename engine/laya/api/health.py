# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""GET /health — System health check endpoint."""

import time
from typing import Any

import structlog
from fastapi import APIRouter

from laya.config import get_n8n_config
from laya.db.chromadb_store import get_chromadb_status, get_embedding_info
from laya.db.sqlite import is_healthy as sqlite_healthy
from laya.http_client import get_client
from laya.llm import model_availability

log = structlog.get_logger()
router = APIRouter()

_start_time = time.time()


async def _models_status() -> dict[str, Any] | None:
    """Return model_availability.status(), or None on error.

    Tauri polls /health for startup readiness (sidecar.rs:1138), so it must never 500.
    """
    try:
        return await model_availability.status()
    except Exception as e:
        log.warning("health_models_status_failed", error=str(e))
        return None


@router.get("/health")
async def health_check() -> dict:
    """Check engine, SQLite, ChromaDB, n8n and model health status."""
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
        "models": await _models_status(),
    }
