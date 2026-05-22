# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""API router for tags CRUD and assignment."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from laya.db.sqlite import get_db
from laya.models.card import TagAssignment, TagResponse

log = structlog.get_logger()
router = APIRouter()

TAG_SOFT_CAP = 10


class CreateTagRequest(BaseModel):
    name: str
    color: str | None = None


class UpdateTagRequest(BaseModel):
    name: str | None = None
    color: str | None = None


class AssignTagRequest(BaseModel):
    tag_name_or_id: str | int
    target_type: str
    target_id: str
    create_if_missing: bool = True


class UnassignTagRequest(BaseModel):
    tag_id: int
    target_type: str
    target_id: str


async def _resolve_tag(tag_name_or_id: str | int, create_if_missing: bool = False) -> dict | None:
    """Resolve a tag by ID (int) or name (str). Optionally create if missing."""
    db = await get_db()
    if isinstance(tag_name_or_id, int) or (isinstance(tag_name_or_id, str) and tag_name_or_id.isdigit()):
        rows = await db.execute_fetchall(
            "SELECT tag_id, name, color, is_system FROM tags WHERE tag_id = ?",
            (int(tag_name_or_id),),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT tag_id, name, color, is_system FROM tags WHERE name = ?",
            (tag_name_or_id,),
        )
    if rows:
        return dict(rows[0])
    if create_if_missing and isinstance(tag_name_or_id, str):
        name = tag_name_or_id.strip().lower()
        if not name:
            return None
        await db.execute(
            "INSERT INTO tags (name) VALUES (?)",
            (name,),
        )
        await db.commit()
        new_rows = await db.execute_fetchall(
            "SELECT tag_id, name, color, is_system FROM tags WHERE name = ?",
            (name,),
        )
        return dict(new_rows[0]) if new_rows else None
    return None


@router.get("/tags")
async def list_tags(is_system: bool | None = Query(None)):
    db = await get_db()
    if is_system is not None:
        rows = await db.execute_fetchall(
            "SELECT tag_id, name, color, is_system, created_at FROM tags WHERE is_system = ? ORDER BY name",
            (1 if is_system else 0,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT tag_id, name, color, is_system, created_at FROM tags ORDER BY name"
        )
    return {
        "tags": [
            TagResponse(
                tag_id=r["tag_id"],
                name=r["name"],
                color=r["color"],
                is_system=bool(r["is_system"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
    }


@router.post("/tags", status_code=201)
async def create_tag(req: CreateTagRequest):
    name = req.name.strip().lower()
    if not name:
        raise HTTPException(400, "Tag name cannot be empty")
    if len(name) > 50:
        raise HTTPException(400, "Tag name too long (max 50 chars)")
    db = await get_db()
    existing = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
    if existing:
        raise HTTPException(409, f"Tag '{name}' already exists")
    await db.execute(
        "INSERT INTO tags (name, color) VALUES (?, ?)",
        (name, req.color),
    )
    await db.commit()
    rows = await db.execute_fetchall(
        "SELECT tag_id, name, color, is_system, created_at FROM tags WHERE name = ?",
        (name,),
    )
    r = rows[0]
    return TagResponse(
        tag_id=r["tag_id"],
        name=r["name"],
        color=r["color"],
        is_system=bool(r["is_system"]),
        created_at=r["created_at"],
    )


@router.put("/tags/{tag_id}")
async def update_tag(tag_id: int, req: UpdateTagRequest):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM tags WHERE tag_id = ?", (tag_id,))
    if not rows:
        raise HTTPException(404, "Tag not found")
    tag = rows[0]
    if req.name is not None and tag["is_system"]:
        raise HTTPException(403, "Cannot rename system tags")
    updates = []
    params = []
    if req.name is not None:
        name = req.name.strip().lower()
        if not name:
            raise HTTPException(400, "Tag name cannot be empty")
        updates.append("name = ?")
        params.append(name)
    if req.color is not None:
        updates.append("color = ?")
        params.append(req.color)
    if not updates:
        raise HTTPException(400, "No fields to update")
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(tag_id)
    await db.execute(f"UPDATE tags SET {', '.join(updates)} WHERE tag_id = ?", tuple(params))
    await db.commit()
    updated = await db.execute_fetchall(
        "SELECT tag_id, name, color, is_system, created_at FROM tags WHERE tag_id = ?",
        (tag_id,),
    )
    r = updated[0]
    return TagResponse(
        tag_id=r["tag_id"],
        name=r["name"],
        color=r["color"],
        is_system=bool(r["is_system"]),
        created_at=r["created_at"],
    )


@router.post("/tags/assign")
async def assign_tag(req: AssignTagRequest):
    if req.target_type not in ("card", "entity", "context"):
        raise HTTPException(400, "target_type must be 'card', 'entity', or 'context'")
    tag = await _resolve_tag(req.tag_name_or_id, create_if_missing=req.create_if_missing)
    if not tag:
        raise HTTPException(404, f"Tag '{req.tag_name_or_id}' not found")
    db = await get_db()
    # Enforce soft cap
    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM tag_assignments WHERE target_type = ? AND target_id = ?",
        (req.target_type, req.target_id),
    )
    if count_rows[0]["cnt"] >= TAG_SOFT_CAP:
        raise HTTPException(
            422, f"Tag cap ({TAG_SOFT_CAP}) reached for this {req.target_type}"
        )
    await db.execute(
        "INSERT OR IGNORE INTO tag_assignments (tag_id, target_type, target_id, assigned_by) VALUES (?, ?, ?, 'user')",
        (tag["tag_id"], req.target_type, req.target_id),
    )
    await db.commit()
    # Update ChromaDB for cards
    if req.target_type == "card":
        from laya.pipeline.tags import update_card_tags_in_chromadb
        try:
            await update_card_tags_in_chromadb(req.target_id)
        except Exception as e:
            log.warning("tag_assign_chromadb_failed", card_id=req.target_id, error=str(e))
    # Broadcast update
    from laya.api.websocket import manager
    await manager.broadcast({
        "type": "tags_changed",
        "payload": {
            "target_type": req.target_type,
            "target_id": req.target_id,
            "tag_id": tag["tag_id"],
            "tag_name": tag["name"],
            "action": "assigned",
        },
    })
    return {"status": "assigned", "tag_id": tag["tag_id"], "tag_name": tag["name"]}


# Literal path must be registered before /tags/{tag_id} so FastAPI doesn't match "unassign" as a tag_id
@router.delete("/tags/unassign")
async def unassign_tag(req: UnassignTagRequest):
    if req.target_type not in ("card", "entity", "context"):
        raise HTTPException(400, "target_type must be 'card', 'entity', or 'context'")
    db = await get_db()
    await db.execute(
        "DELETE FROM tag_assignments WHERE tag_id = ? AND target_type = ? AND target_id = ?",
        (req.tag_id, req.target_type, req.target_id),
    )
    await db.commit()
    # Update ChromaDB for cards
    if req.target_type == "card":
        from laya.pipeline.tags import update_card_tags_in_chromadb
        try:
            await update_card_tags_in_chromadb(req.target_id)
        except Exception as e:
            log.warning("tag_unassign_chromadb_failed", card_id=req.target_id, error=str(e))
    from laya.api.websocket import manager
    await manager.broadcast({
        "type": "tags_changed",
        "payload": {
            "target_type": req.target_type,
            "target_id": req.target_id,
            "tag_id": req.tag_id,
            "action": "removed",
        },
    })
    return {"status": "removed"}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT is_system FROM tags WHERE tag_id = ?", (tag_id,))
    if not rows:
        raise HTTPException(404, "Tag not found")
    if rows[0]["is_system"]:
        raise HTTPException(403, "Cannot delete system tags")
    # Collect affected card IDs for ChromaDB update
    affected = await db.execute_fetchall(
        "SELECT target_id FROM tag_assignments WHERE tag_id = ? AND target_type = 'card'",
        (tag_id,),
    )
    await db.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
    await db.commit()
    # Update ChromaDB metadata for affected cards
    if affected:
        from laya.pipeline.tags import update_card_tags_in_chromadb
        for row in affected:
            try:
                await update_card_tags_in_chromadb(row["target_id"])
            except Exception as e:
                log.warning("tag_delete_chromadb_update_failed", card_id=row["target_id"], error=str(e))
    return {"status": "deleted"}


@router.get("/tags/for/{target_type}/{target_id:path}")
async def get_tags_for_target(target_type: str, target_id: str):
    if target_type not in ("card", "entity", "context"):
        raise HTTPException(400, "target_type must be 'card', 'entity', or 'context'")
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT t.tag_id, t.name, t.color, t.is_system, ta.assigned_by
           FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id
           WHERE ta.target_type = ? AND ta.target_id = ?
           ORDER BY t.name""",
        (target_type, target_id),
    )
    return {
        "tags": [
            TagAssignment(
                tag_id=r["tag_id"],
                tag_name=r["name"],
                color=r["color"],
                is_system=bool(r["is_system"]),
                assigned_by=r["assigned_by"],
            )
            for r in rows
        ]
    }
