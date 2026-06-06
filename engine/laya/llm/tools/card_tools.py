# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Card-related tool implementations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.llm.tools.constants import (
    CARDS_BY_ENTITY_DEFAULT,
    CARDS_BY_ENTITY_MAX,
    CHAT_SEARCH_DEFAULT,
    CHAT_SEARCH_MAX,
)


def _parse_entity_platform(entity_id: str) -> str:
    """Extract the platform prefix from 'platform:subject_type:subject_id'."""
    return entity_id.split(":", 1)[0] if entity_id else ""


async def search_cards(
    query: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    limit: int = CHAT_SEARCH_DEFAULT,
    offset: int = 0,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Search action cards by keyword, status, priority.

    Results are grouped by entity so the caller sees which cards belong
    together.  Each group carries a lean rolling summary (headline,
    current_status, pending_actions) when one exists, giving the full
    picture even if the search only matched a subset of the entity's cards.
    """
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []

    if query:
        keywords = [w for w in query.split() if len(w) >= 2]
        for kw in keywords[:8]:
            conditions.append("(header LIKE ? OR summary LIKE ? OR intelligence LIKE ?)")
            params.extend([f"%{kw}%"] * 3)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if priority:
        conditions.append("priority = ?")
        params.append(priority)

    if space_id:
        conditions.append("space_id = ?")
        params.append(space_id)

    where = " AND ".join(conditions) if conditions else "1=1"

    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) as cnt FROM action_cards WHERE {where}",
        params,
    )
    total = count_rows[0]["cnt"] if count_rows else 0

    capped_limit = min(limit, CHAT_SEARCH_MAX)
    rows = await db.execute_fetchall(
        f"""SELECT card_id, event_id, entity_id, context_id, header, summary,
                   status, priority, persona, category, created_at, space_id
            FROM action_cards WHERE {where}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        [*params, capped_limit, offset],
    )

    # -- Group rows by entity_id ------------------------------------------
    from collections import OrderedDict

    groups_map: OrderedDict[str | None, list[dict[str, Any]]] = OrderedDict()
    for r in rows:
        eid = r["entity_id"] or None
        card = {
            "card_id": r["card_id"],
            "event_id": r["event_id"],
            "header": r["header"],
            "summary": r["summary"],
            "status": r["status"],
            "priority": r["priority"],
            "persona": r["persona"],
            "category": r["category"],
            "created_at": r["created_at"],
        }
        groups_map.setdefault(eid, []).append(card)

    # Distinct non-null entity_ids on this page
    entity_ids = [eid for eid in groups_map if eid is not None]

    # -- Batch-fetch group summaries --------------------------------------
    summary_map: dict[str, dict[str, Any]] = {}
    if entity_ids:
        ph = ",".join("?" * len(entity_ids))
        summary_rows = await db.execute_fetchall(
            f"SELECT entity_id, headline, current_status, pending_actions "
            f"FROM group_summaries WHERE entity_id IN ({ph})",
            entity_ids,
        )
        for sr in summary_rows:
            pending = sr["pending_actions"]
            if pending:
                try:
                    pending = json.loads(pending)
                except json.JSONDecodeError:
                    pass
            summary_map[sr["entity_id"]] = {
                "headline": sr["headline"],
                "current_status": sr["current_status"],
                "pending_actions": pending,
            }

    # -- Batch-fetch total card counts per entity -------------------------
    entity_total_map: dict[str, int] = {}
    if entity_ids:
        ph = ",".join("?" * len(entity_ids))
        count_rows2 = await db.execute_fetchall(
            f"SELECT entity_id, COUNT(*) as cnt FROM action_cards "
            f"WHERE entity_id IN ({ph}) GROUP BY entity_id",
            entity_ids,
        )
        for cr in count_rows2:
            entity_total_map[cr["entity_id"]] = cr["cnt"]

    # -- Build grouped response -------------------------------------------
    groups: list[dict[str, Any]] = []
    for eid, cards in groups_map.items():
        if eid is not None:
            group: dict[str, Any] = {
                "entity_id": eid,
                "entity_title": summary_map[eid]["headline"] if eid in summary_map else cards[0]["header"],
                "platform": _parse_entity_platform(eid),
                "matched_cards": len(cards),
                "total_entity_cards": entity_total_map.get(eid, len(cards)),
                "group_summary": summary_map.get(eid),
                "cards": cards,
            }
        else:
            # Singletons — cards with no entity_id get their own group each
            for card in cards:
                group = {
                    "entity_id": None,
                    "entity_title": card["header"],
                    "platform": card["category"],
                    "matched_cards": 1,
                    "total_entity_cards": 1,
                    "group_summary": None,
                    "cards": [card],
                }
                groups.append(group)
            continue
        groups.append(group)

    return {
        "groups": groups,
        "total_cards": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


async def get_card(card_id: str) -> dict[str, Any]:
    """Get full details of a specific card."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT card_id, event_id, entity_id, context_id, header, summary,
                  intelligence, staged_output, suggested_actions, status, priority,
                  persona, category, created_at, resolved_at,
                  confidence, space_id, has_workspace
           FROM action_cards WHERE card_id = ?""",
        (card_id,),
    )
    if not rows:
        return {"error": f"Card '{card_id}' not found"}

    r = rows[0]
    card: dict[str, Any] = {
        "card_id": r["card_id"],
        "event_id": r["event_id"],
        "entity_id": r["entity_id"],
        "context_id": r["context_id"],
        "header": r["header"],
        "summary": r["summary"],
        "intelligence": r["intelligence"],
        "staged_output": r["staged_output"],
        "status": r["status"],
        "priority": r["priority"],
        "persona": r["persona"],
        "category": r["category"],
        "created_at": r["created_at"],
        "resolved_at": r["resolved_at"],
        "confidence": r["confidence"],
        "space_id": r["space_id"],
        "has_workspace": bool(r["has_workspace"]),
    }

    # Parse suggested actions JSON
    if r["suggested_actions"]:
        try:
            card["suggested_actions"] = json.loads(r["suggested_actions"])
        except json.JSONDecodeError:
            card["suggested_actions"] = r["suggested_actions"]

    return {"card": card}


async def get_card_stats(space_id: str | None = None) -> dict[str, Any]:
    """Get summary statistics about action cards."""
    db = await get_db()
    space_filter = "WHERE space_id = ?" if space_id else ""
    space_params: tuple = (space_id,) if space_id else ()

    # Status counts
    status_rows = await db.execute_fetchall(
        f"SELECT status, COUNT(*) as cnt FROM action_cards {space_filter} GROUP BY status",
        space_params,
    )
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    # Priority counts
    priority_rows = await db.execute_fetchall(
        f"SELECT priority, COUNT(*) as cnt FROM action_cards {space_filter} GROUP BY priority",
        space_params,
    )
    by_priority = {r["priority"]: r["cnt"] for r in priority_rows}

    # Category counts
    cat_rows = await db.execute_fetchall(
        f"SELECT category, COUNT(*) as cnt FROM action_cards {space_filter} GROUP BY category ORDER BY cnt DESC LIMIT 10",
        space_params,
    )
    by_category = {r["category"]: r["cnt"] for r in cat_rows}

    total = sum(by_status.values())

    return {
        "total_cards": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_category": by_category,
    }


async def get_cards_for_event(event_id: str) -> dict[str, Any]:
    """Get all cards generated from a specific event."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT card_id, entity_id, context_id, header, summary, status,
                  priority, persona, category, created_at
           FROM action_cards WHERE event_id = ?
           ORDER BY created_at DESC""",
        (event_id,),
    )
    return {
        "event_id": event_id,
        "cards": [
            {
                "card_id": r["card_id"],
                "entity_id": r["entity_id"],
                "context_id": r["context_id"],
                "header": r["header"],
                "summary": r["summary"],
                "status": r["status"],
                "priority": r["priority"],
                "persona": r["persona"],
                "category": r["category"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


async def get_cards_by_entity(
    entity_id: str,
    limit: int = CARDS_BY_ENTITY_DEFAULT,
    offset: int = 0,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Get all action cards that belong to a specific entity."""
    db = await get_db()
    capped_limit = min(limit, CARDS_BY_ENTITY_MAX)

    if space_id:
        count_rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM action_cards WHERE entity_id = ? AND space_id = ?",
            (entity_id, space_id),
        )
        rows = await db.execute_fetchall(
            """SELECT card_id, event_id, entity_id, context_id, header, summary,
                      intelligence, status, priority, persona, category, created_at
               FROM action_cards WHERE entity_id = ? AND space_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (entity_id, space_id, capped_limit, offset),
        )
    else:
        count_rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM action_cards WHERE entity_id = ?",
            (entity_id,),
        )
        rows = await db.execute_fetchall(
            """SELECT card_id, event_id, entity_id, context_id, header, summary,
                      intelligence, status, priority, persona, category, created_at
               FROM action_cards WHERE entity_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (entity_id, capped_limit, offset),
        )

    total = count_rows[0]["cnt"] if count_rows else 0
    return {
        "entity_id": entity_id,
        "cards": [
            {
                "card_id": r["card_id"],
                "event_id": r["event_id"],
                "entity_id": r["entity_id"],
                "context_id": r["context_id"],
                "header": r["header"],
                "summary": r["summary"],
                "intelligence": r["intelligence"],
                "status": r["status"],
                "priority": r["priority"],
                "persona": r["persona"],
                "category": r["category"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
        "total": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


async def _update_card_status(card_id: str, new_status: str) -> dict[str, Any]:
    """Update a card's status, broadcast to frontend, and return result."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    rows = await db.execute_fetchall(
        "SELECT card_id, status FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        return {"error": f"Card '{card_id}' not found"}

    old_status = rows[0]["status"]
    resolved_at = now if new_status in ("dismissed", "done", "archived") else None

    await db.execute(
        """UPDATE action_cards
           SET status = ?, resolved_at = COALESCE(?, resolved_at), updated_at = ?
           WHERE card_id = ?""",
        (new_status, resolved_at, now, card_id),
    )
    await db.commit()

    # Broadcast status change to frontend so UI updates in real-time
    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": new_status}}
    )

    # Mirror the audit trail the REST card-lifecycle endpoints write (cards_api.py),
    # so a card mutated via chat is recorded the same as one mutated via the UI.
    # source="chat" distinguishes the path. Local import avoids an import cycle with
    # the LLM client, matching the lazy-import style used elsewhere in this module.
    from laya.llm.client import log_to_audit

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": new_status, "previous_status": old_status, "source": "chat"},
    )

    return {
        "card_id": card_id,
        "old_status": old_status,
        "new_status": new_status,
        "success": True,
    }


async def dismiss_card(card_id: str) -> dict[str, Any]:
    """Dismiss a card."""
    return await _update_card_status(card_id, "dismissed")


async def mark_card_done(card_id: str) -> dict[str, Any]:
    """Mark a card as done."""
    return await _update_card_status(card_id, "done")



async def archive_card(card_id: str) -> dict[str, Any]:
    """Archive a card."""
    return await _update_card_status(card_id, "archived")


async def reopen_card(card_id: str) -> dict[str, Any]:
    """Reopen a dismissed/archived card back to pending."""
    return await _update_card_status(card_id, "pending")
