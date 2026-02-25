"""Audit log REST API — filterable, paginated audit log entries."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()


@router.get("/audit-log")
async def get_audit_log(
    step: str | None = None,
    event_id: str | None = None,
    card_id: str | None = None,
    success: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return paginated, filterable audit log entries."""
    db = await get_db()

    conditions: list[str] = []
    params: list[Any] = []

    if step:
        conditions.append("step = ?")
        params.append(step)
    if event_id:
        conditions.append("event_id = ?")
        params.append(event_id)
    if card_id:
        conditions.append("card_id = ?")
        params.append(card_id)
    if success is not None:
        conditions.append("success = ?")
        params.append(success)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Count total
    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM audit_log {where_clause}", params
    )
    total = count_rows[0][0]

    # Fetch page
    rows = await db.execute_fetchall(
        f"""SELECT log_id, timestamp, event_id, card_id, step, model_used,
                   input_tokens, output_tokens, latency_ms, success, error, metadata
            FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )

    entries = []
    for row in rows:
        metadata = None
        if row[11]:
            try:
                metadata = json.loads(row[11])
            except json.JSONDecodeError:
                metadata = None

        entries.append({
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
        })

    return {
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
