# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Classification rules & corrections API."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.db.sqlite import get_db
from laya.db.timeutil import db_now

log = structlog.get_logger()
router = APIRouter(prefix="/classification")


# ── Request / Response models ────────────────────────────────────────────

class CreateRuleRequest(BaseModel):
    rule_text: str
    field: str | None = None  # 'priority' | 'persona' | None (general)
    space_id: str | None = None


class UpdateRuleRequest(BaseModel):
    rule_text: str | None = None
    field: str | None = None
    active: bool | None = None


# ── Rules CRUD ───────────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(space_id: str | None = None, field: str | None = None, active: bool | None = None) -> list[dict]:
    """List classification rules with optional filters."""
    db = await get_db()
    clauses = []
    params: list = []

    if space_id is not None:
        clauses.append("space_id = ?")
        params.append(space_id)
    if field is not None:
        clauses.append("field = ?")
        params.append(field)
    if active is not None:
        clauses.append("active = ?")
        params.append(int(active))

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.execute_fetchall(
        f"SELECT * FROM classification_rules{where} ORDER BY created_at DESC",
        tuple(params),
    )

    return [
        {
            "id": r["id"],
            "space_id": r["space_id"],
            "field": r["field"],
            "rule_text": r["rule_text"],
            "source": r["source"],
            "active": bool(r["active"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@router.post("/rules")
async def create_rule(body: CreateRuleRequest) -> dict:
    """Create a new classification rule."""
    db = await get_db()
    now = db_now()
    cursor = await db.execute(
        """INSERT INTO classification_rules (space_id, field, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, ?, 'manual', 1, ?, ?)""",
        (body.space_id, body.field, body.rule_text, now, now),
    )
    await db.commit()
    rule_id = cursor.lastrowid
    log.info("classification_rule_created", rule_id=rule_id, field=body.field)
    return {"id": rule_id, "status": "created"}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: UpdateRuleRequest) -> dict:
    """Update an existing classification rule."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT id FROM classification_rules WHERE id = ?", (rule_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    updates = []
    params: list = []
    if body.rule_text is not None:
        updates.append("rule_text = ?")
        params.append(body.rule_text)
    if body.field is not None:
        updates.append("field = ?")
        params.append(body.field)
    if body.active is not None:
        updates.append("active = ?")
        params.append(int(body.active))

    if not updates:
        return {"status": "no_changes"}

    updates.append("updated_at = ?")
    params.append(db_now())
    params.append(rule_id)

    await db.execute(
        f"UPDATE classification_rules SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    await db.commit()
    log.info("classification_rule_updated", rule_id=rule_id)
    return {"id": rule_id, "status": "updated"}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int) -> dict:
    """Delete a classification rule."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM classification_rules WHERE id = ?", (rule_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.execute("DELETE FROM classification_rules WHERE id = ?", (rule_id,))
    await db.commit()
    log.info("classification_rule_deleted", rule_id=rule_id)
    return {"id": rule_id, "status": "deleted"}


# ── Corrections list ─────────────────────────────────────────────────────

@router.get("/corrections")
async def list_corrections(space_id: str | None = None, limit: int = 50) -> list[dict]:
    """List recent classification corrections."""
    db = await get_db()
    if space_id:
        rows = await db.execute_fetchall(
            "SELECT * FROM classification_corrections WHERE space_id = ? ORDER BY created_at DESC LIMIT ?",
            (space_id, limit),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM classification_corrections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    return [
        {
            "id": r["id"],
            "card_id": r["card_id"],
            "space_id": r["space_id"],
            "field": r["field"],
            "original_value": r["original_value"],
            "corrected_value": r["corrected_value"],
            "card_summary": r["card_summary"],
            "category": r["category"],
            "platform": r["platform"],
            "event_type": r["event_type"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
