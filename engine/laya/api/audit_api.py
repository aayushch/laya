# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Audit log REST API — filterable, paginated audit log entries."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()

# Columns selected for both the list and export endpoints, in a stable order.
_AUDIT_COLUMNS = (
    "log_id, timestamp, event_id, card_id, step, model_used, "
    "input_tokens, output_tokens, latency_ms, success, error, metadata"
)


def utc_cutoff(days: int) -> str | None:
    """Return a UTC cutoff string `days` ago, or None for all-time (days<=0).

    Formatted as `YYYY-MM-DD HH:MM:SS` to match SQLite's CURRENT_TIMESTAMP
    storage so the comparison stays a correct lexicographic string compare.
    """
    if days <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _audit_filter_conditions(
    step: str | None,
    event_id: str | None,
    card_id: str | None,
    success: bool | None,
    search: str | None,
) -> tuple[list[str], list[Any]]:
    """Build WHERE conditions + params shared by the list and export endpoints."""
    conditions: list[str] = []
    params: list[Any] = []

    if step:
        steps = [s.strip() for s in step.split(",") if s.strip()]
        if len(steps) == 1:
            conditions.append("step = ?")
            params.append(steps[0])
        elif steps:
            placeholders = ",".join("?" for _ in steps)
            conditions.append(f"step IN ({placeholders})")
            params.extend(steps)
    if event_id:
        conditions.append("event_id = ?")
        params.append(event_id)
    if card_id:
        conditions.append("card_id = ?")
        params.append(card_id)
    if success is not None:
        conditions.append("success = ?")
        params.append(success)
    if search:
        conditions.append(
            "(step LIKE ? OR model_used LIKE ? OR error LIKE ? OR event_id LIKE ? OR card_id LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like])

    return conditions, params


def _shape_audit_row(row: Any) -> dict[str, Any]:
    """Map a raw audit_log row (in _AUDIT_COLUMNS order) to a response dict."""
    metadata = None
    if row[11]:
        try:
            metadata = json.loads(row[11])
        except json.JSONDecodeError:
            metadata = None

    return {
        "log_id": row[0],
        "timestamp": row[1],
        "event_id": row[2],
        "card_id": row[3],
        "step": row[4],
        "model_used": row[5],
        "input_tokens": row[6] or 0,
        "output_tokens": row[7] or 0,
        "latency_ms": row[8] or 0,
        "success": bool(row[9]),
        "error": row[10],
        "metadata": metadata,
    }


@router.get("/audit-log")
async def get_audit_log(
    step: str | None = None,
    event_id: str | None = None,
    card_id: str | None = None,
    success: bool | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return paginated, filterable audit log entries."""
    db = await get_db()

    conditions, params = _audit_filter_conditions(step, event_id, card_id, success, search)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count total
    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM audit_log {where_clause}", params
    )
    total = count_rows[0][0]

    # Fetch page
    rows = await db.execute_fetchall(
        f"""SELECT {_AUDIT_COLUMNS}
            FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )

    return {
        "entries": [_shape_audit_row(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/audit-log/export")
async def export_audit_log(
    days: int = 0,
    step: str | None = None,
    success: bool | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Export audit log entries as JSON, optionally limited to the last `days`.

    days=0 (default) exports all time. Honors the same step/success/search
    filters as the list endpoint so the export matches the current view.
    Unpaginated — returns every matching row.
    """
    db = await get_db()

    conditions, params = _audit_filter_conditions(step, None, None, success, search)
    since = utc_cutoff(days)
    if since is not None:
        conditions.append("timestamp >= ?")
        params.append(since)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.execute_fetchall(
        f"""SELECT {_AUDIT_COLUMNS}
            FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC""",
        params,
    )

    entries = [_shape_audit_row(row) for row in rows]
    log.info("audit_log_exported", count=len(entries), days=days)
    return {
        "kind": "audit_log",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "since": since,
        "count": len(entries),
        "entries": entries,
    }


# ── failure summary (drives the Audit/Settings red dot) ──────────────────────


async def compute_failure_counts(db: Any) -> dict[str, int]:
    """Count outstanding failures surfaced in the Audit panel.

    Two sources, mirroring what the Audit tab shows by default:
      - dead_events:      events that exhausted all retries (processing_status='dead')
      - ingestion_errors: uncleared n8n ingestion failures (cleared_at IS NULL,
                          matching list_ingestion_errors' default filter)

    Shared by GET /audit/failure-summary (startup seed) and the WebSocket
    `audit_failure` broadcasts so the client never has to poll for these.
    """
    dead_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM events WHERE processing_status = 'dead'"
    )
    ingestion_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM ingestion_errors WHERE cleared_at IS NULL"
    )
    return {
        "dead_events": dead_rows[0][0] if dead_rows else 0,
        "ingestion_errors": ingestion_rows[0][0] if ingestion_rows else 0,
    }


@router.get("/audit/failure-summary")
async def get_failure_summary() -> dict[str, Any]:
    """One-shot count of outstanding failures, fetched once on app startup.

    The client seeds its red-dot indicator from this, then stays in sync via the
    `audit_failure` WebSocket push — no background polling of these tables.
    """
    db = await get_db()
    counts = await compute_failure_counts(db)
    return {
        **counts,
        "has_failures": (counts["dead_events"] + counts["ingestion_errors"]) > 0,
    }
