# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared tag helpers — used by emit, processing rules, and tags API."""

from __future__ import annotations

import structlog

from laya.db.sqlite import get_db

log = structlog.get_logger()

TAG_SOFT_CAP = 10


async def persist_suggested_tags(card_id: str, tag_names: list[str]) -> list[str]:
    """Get-or-create tags and assign to a card. Returns the names actually assigned."""
    db = await get_db()
    assigned: list[str] = []

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM tag_assignments WHERE target_type = 'card' AND target_id = ?",
        (card_id,),
    )
    current_count = count_rows[0]["cnt"]

    for raw_name in tag_names[:3]:
        name = raw_name.strip().lower()
        if not name or len(name) > 50:
            continue
        if current_count >= TAG_SOFT_CAP:
            break
        rows = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
        if rows:
            tag_id = rows[0]["tag_id"]
        else:
            await db.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            await db.commit()
            new = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
            tag_id = new[0]["tag_id"]
        await db.execute(
            "INSERT OR IGNORE INTO tag_assignments (tag_id, target_type, target_id, assigned_by) VALUES (?, 'card', ?, 'stager')",
            (tag_id, card_id),
        )
        assigned.append(name)
        current_count += 1

    if assigned:
        await db.commit()
    return assigned


async def get_tags_csv(card_id: str) -> str:
    """Return comma-separated tag names for a card (for ChromaDB metadata)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT t.name FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND ta.target_id = ?",
        (card_id,),
    )
    return ",".join(r["name"] for r in rows) if rows else ""


async def update_card_tags_in_chromadb(card_id: str) -> None:
    """Re-upsert ChromaDB metadata with current tags for a card."""
    from laya.db.chromadb_store import get_collection

    tags_csv = await get_tags_csv(card_id)
    try:
        collection = get_collection()
        existing = collection.get(ids=[card_id], include=["metadatas"])
        if existing and existing["metadatas"]:
            metadata = existing["metadatas"][0]
            metadata["tags"] = tags_csv
            collection.update(ids=[card_id], metadatas=[metadata])
    except Exception as e:
        log.warning("chromadb_tag_update_failed", card_id=card_id, error=str(e))


async def get_card_tag_names(card_id: str) -> list[str]:
    """Return tag names for a card."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT t.name FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND ta.target_id = ?",
        (card_id,),
    )
    return [r["name"] for r in rows]


async def batch_load_tags(
    card_ids: list[str], entity_ids: list[str] | None = None
) -> dict[tuple[str, str], list[dict]]:
    """Batch-load tags for cards and entity groups. Returns {(target_type, target_id): [tag dicts]}."""
    db = await get_db()
    result: dict[tuple[str, str], list[dict]] = {}
    if not card_ids and not entity_ids:
        return result

    conditions = []
    params: list[str] = []
    if card_ids:
        placeholders = ",".join("?" * len(card_ids))
        conditions.append(f"(ta.target_type = 'card' AND ta.target_id IN ({placeholders}))")
        params.extend(card_ids)
    if entity_ids:
        placeholders = ",".join("?" * len(entity_ids))
        conditions.append(f"(ta.target_type = 'entity' AND ta.target_id IN ({placeholders}))")
        params.extend(entity_ids)

    query = f"""
        SELECT ta.target_type, ta.target_id, t.tag_id, t.name, t.color, t.is_system, ta.assigned_by
        FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id
        WHERE {' OR '.join(conditions)}
        ORDER BY t.name
    """
    rows = await db.execute_fetchall(query, tuple(params))
    for r in rows:
        key = (r["target_type"], r["target_id"])
        if key not in result:
            result[key] = []
        result[key].append({
            "tag_id": r["tag_id"],
            "tag_name": r["name"],
            "color": r["color"],
            "is_system": bool(r["is_system"]),
            "assigned_by": r["assigned_by"],
        })
    return result
