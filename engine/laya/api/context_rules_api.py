# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Context grouping rules API.

Surfaces the learned/manual context-association rules (the `context_rules`
table) for the user to view, edit, delete, and add. Mirrors
``classification_api`` but has no ``field`` column and paginates the list
because learned rules can grow large.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.db.sqlite import get_db
from laya.db.timeutil import db_now

log = structlog.get_logger()
router = APIRouter(prefix="/context-rules")


# ── Request models ─────────────────────────────────────────────────────────

class CreateContextRuleRequest(BaseModel):
    rule_text: str
    space_id: str | None = None


class UpdateContextRuleRequest(BaseModel):
    rule_text: str | None = None
    active: bool | None = None


# ── Rules CRUD ─────────────────────────────────────────────────────────────

@router.get("")
async def list_context_rules(
    space_id: str | None = None,
    source: str | None = None,
    active: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List context rules (paginated) with optional filters."""
    db = await get_db()
    clauses: list[str] = []
    params: list = []

    if space_id is not None:
        clauses.append("space_id = ?")
        params.append(space_id)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if active is not None:
        clauses.append("active = ?")
        params.append(int(active))

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM context_rules{where}", tuple(params)
    )
    total = count_rows[0][0]

    rows = await db.execute_fetchall(
        f"SELECT * FROM context_rules{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )

    return {
        "rules": [
            {
                "id": r["id"],
                "space_id": r["space_id"],
                "rule_text": r["rule_text"],
                "source": r["source"],
                "active": bool(r["active"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("")
async def create_context_rule(body: CreateContextRuleRequest) -> dict:
    """Create a new manual context rule."""
    rule_text = body.rule_text.strip()
    if not rule_text:
        raise HTTPException(status_code=400, detail="rule_text is required")

    db = await get_db()
    now = db_now()
    cursor = await db.execute(
        """INSERT INTO context_rules (space_id, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, 'manual', 1, ?, ?)""",
        (body.space_id, rule_text, now, now),
    )
    await db.commit()
    rule_id = cursor.lastrowid
    log.info("context_rule_created", rule_id=rule_id)
    return {"id": rule_id, "status": "created"}


@router.put("/{rule_id}")
async def update_context_rule(rule_id: int, body: UpdateContextRuleRequest) -> dict:
    """Update an existing context rule (text and/or active flag)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT id FROM context_rules WHERE id = ?", (rule_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    updates: list[str] = []
    params: list = []
    if body.rule_text is not None:
        updates.append("rule_text = ?")
        params.append(body.rule_text)
    if body.active is not None:
        updates.append("active = ?")
        params.append(int(body.active))

    if not updates:
        return {"status": "no_changes"}

    updates.append("updated_at = ?")
    params.append(db_now())
    params.append(rule_id)

    await db.execute(
        f"UPDATE context_rules SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    await db.commit()
    log.info("context_rule_updated", rule_id=rule_id)
    return {"id": rule_id, "status": "updated"}


@router.delete("/{rule_id}")
async def delete_context_rule(rule_id: int) -> dict:
    """Delete a context rule."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM context_rules WHERE id = ?", (rule_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.execute("DELETE FROM context_rules WHERE id = ?", (rule_id,))
    await db.commit()
    log.info("context_rule_deleted", rule_id=rule_id)
    return {"id": rule_id, "status": "deleted"}
