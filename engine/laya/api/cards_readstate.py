# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Card read-state + bookmark endpoints (split from cards_api — P7-6)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now

log = structlog.get_logger()
router = APIRouter()


@router.post("/cards/{card_id}/bookmark")
async def bookmark_card(card_id: str) -> dict:
    """Bookmark a card for later."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    now = db_now()
    await db.execute(
        "UPDATE action_cards SET bookmarked_at = ?, read_at = COALESCE(read_at, ?) WHERE card_id = ?",
        (now, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"bookmarked_at": now}}
    )
    log.info("card_bookmarked", card_id=card_id)
    return {"status": "bookmarked", "card_id": card_id, "bookmarked_at": now}


@router.post("/cards/{card_id}/unbookmark")
async def unbookmark_card(card_id: str) -> dict:
    """Remove bookmark from a card."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    await db.execute(
        "UPDATE action_cards SET bookmarked_at = NULL WHERE card_id = ?",
        (card_id,),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"bookmarked_at": None}}
    )
    log.info("card_unbookmarked", card_id=card_id)
    return {"status": "unbookmarked", "card_id": card_id}


@router.post("/cards/{card_id}/read")
async def mark_card_read(card_id: str) -> dict:
    """Mark a single card as read. Idempotent."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT read_at FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")
    if rows[0]["read_at"]:
        return {"status": "already_read", "card_id": card_id, "read_at": rows[0]["read_at"]}

    now = db_now()
    await db.execute(
        "UPDATE action_cards SET read_at = ? WHERE card_id = ? AND read_at IS NULL",
        (now, card_id),
    )
    await db.commit()
    return {"status": "read", "card_id": card_id, "read_at": now}


@router.post("/cards/group/{entity_id:path}/read-all")
async def mark_group_read(entity_id: str) -> dict:
    """Mark all cards in an entity or context group as read."""
    db = await get_db()
    now = db_now()
    if entity_id.startswith("ctx_"):
        cursor = await db.execute(
            "UPDATE action_cards SET read_at = ? WHERE context_id = ? AND read_at IS NULL",
            (now, entity_id),
        )
    else:
        cursor = await db.execute(
            "UPDATE action_cards SET read_at = ? WHERE entity_id = ? AND read_at IS NULL",
            (now, entity_id),
        )
    await db.commit()
    return {"status": "read", "entity_id": entity_id, "marked": cursor.rowcount}


@router.post("/cards/read-all")
async def mark_all_read(
    date: str | None = None,
    space_id: str | None = None,
    tz: str | None = None,
) -> dict:
    """Mark all cards as read, optionally scoped by date and space."""
    db = await get_db()
    now = db_now()
    conditions = ["read_at IS NULL"]
    params: list[Any] = [now]

    if date:
        if tz:
            try:
                local_tz = ZoneInfo(tz)
                local_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=local_tz)
                local_end = local_start + timedelta(days=1)
                utc_start = local_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                utc_end = local_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conditions.append("group_active_at >= ? AND group_active_at < ?")
                params.extend([utc_start, utc_end])
            except (KeyError, ValueError):
                conditions.append("DATE(group_active_at) = ?")
                params.append(date)
        else:
            conditions.append("DATE(group_active_at) = ?")
            params.append(date)

    if space_id:
        space_ids = [s.strip() for s in space_id.split(",") if s.strip()]
        if len(space_ids) == 1:
            conditions.append("space_id = ?")
            params.append(space_ids[0])
        elif space_ids:
            placeholders = ",".join("?" for _ in space_ids)
            conditions.append(f"space_id IN ({placeholders})")
            params.extend(space_ids)

    where = " AND ".join(conditions)
    cursor = await db.execute(
        f"UPDATE action_cards SET read_at = ? WHERE {where}", params
    )
    await db.commit()
    return {"status": "read", "marked": cursor.rowcount}
