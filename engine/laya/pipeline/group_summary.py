"""Group summary pipeline — rolling LLM summaries for entity-id card groups."""

from __future__ import annotations

import json

import structlog

from laya.api.websocket import manager
from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.group_summary import (
    GROUP_SUMMARY_JSON_SCHEMA,
    build_initial_messages,
    build_rolling_messages,
)

log = structlog.get_logger()


async def trigger_group_summary_update(
    entity_id: str,
    new_card_id: str,
    space_id: str | None = None,
) -> None:
    """Generate or update the rolling summary for an entity group.

    Designed to run as a fire-and-forget background task from emit.py.
    """
    settings = load_settings()
    if not settings.get("group_summaries", {}).get("enabled", True):
        return

    db = await get_db()

    try:
        # Count sibling cards (including the new one)
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM action_cards WHERE entity_id = ?",
            (entity_id,),
        ) as cursor:
            count_row = await cursor.fetchone()
        card_count = count_row["cnt"] if count_row else 0
        if card_count < 2:
            return

        # Fetch existing summary
        existing_rows = await db.execute_fetchall(
            "SELECT * FROM group_summaries WHERE entity_id = ?",
            (entity_id,),
        )
        existing_row = existing_rows[0] if existing_rows else None

        if existing_row:
            await _rolling_update(db, entity_id, new_card_id, existing_row, space_id)
        else:
            await _initial_generation(db, entity_id, space_id)

    except Exception:
        log.exception("group_summary_failed", entity_id=entity_id, card_id=new_card_id)


async def regenerate_group_summary(entity_id: str) -> dict | None:
    """Force full regeneration of a group summary from all cards."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT space_id FROM action_cards WHERE entity_id = ? LIMIT 1",
        (entity_id,),
    )
    space_id = rows[0]["space_id"] if rows else "default"
    return await _initial_generation(db, entity_id, space_id=space_id)


async def _initial_generation(
    db,
    entity_id: str,
    space_id: str | None,
) -> dict | None:
    """Generate summary from all cards in the entity group."""
    rows = await db.execute_fetchall(
        """SELECT c.card_id, c.header, c.summary, c.intelligence, c.status,
                  c.created_at, c.entity_id, c.space_id,
                  e.actor_name
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           WHERE c.entity_id = ?
           ORDER BY c.created_at ASC""",
        (entity_id,),
    )
    if len(rows) < 2:
        return None

    cards = [dict(r) for r in rows]
    resolved_space_id = space_id or cards[0].get("space_id") or "default"

    messages = build_initial_messages(cards, entity_id)
    resp = await llm_call(
        role="group_summary",
        messages=messages,
        response_schema=GROUP_SUMMARY_JSON_SCHEMA,
        step="group_summary_initial",
        temperature=0.1,
        max_tokens=1000,
        space_id=resolved_space_id,
    )

    if not resp.parsed:
        log.warning("group_summary_parse_failed", entity_id=entity_id)
        return None

    parsed = resp.parsed
    card_ids = [c["card_id"] for c in cards]

    await db.execute(
        """INSERT INTO group_summaries
               (entity_id, headline, summary, key_events, current_status,
                pending_actions, card_ids, card_count, space_id, model)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(entity_id) DO UPDATE SET
               headline = excluded.headline,
               summary = excluded.summary,
               key_events = excluded.key_events,
               current_status = excluded.current_status,
               pending_actions = excluded.pending_actions,
               card_ids = excluded.card_ids,
               card_count = excluded.card_count,
               model = excluded.model,
               updated_at = datetime('now')""",
        (
            entity_id,
            parsed["headline"],
            parsed["summary"],
            json.dumps(parsed.get("key_events")),
            parsed.get("current_status"),
            json.dumps(parsed.get("pending_actions")),
            json.dumps(card_ids),
            len(card_ids),
            resolved_space_id,
            resp.model,
        ),
    )
    await db.commit()

    summary_data = _build_response_dict(entity_id, parsed, card_ids)
    await manager.broadcast({
        "type": "group_summary_updated",
        "entity_id": entity_id,
        "summary": summary_data,
    })

    log.info(
        "group_summary_generated",
        entity_id=entity_id,
        mode="initial",
        card_count=len(card_ids),
        model=resp.model,
    )
    return summary_data


async def _rolling_update(
    db,
    entity_id: str,
    new_card_id: str,
    existing_row: dict,
    space_id: str | None,
) -> dict | None:
    """Update an existing summary with a new card."""
    card_rows = await db.execute_fetchall(
        """SELECT c.card_id, c.header, c.summary, c.intelligence, c.status,
                  c.created_at, c.entity_id,
                  e.actor_name
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           WHERE c.card_id = ?""",
        (new_card_id,),
    )
    card_row = card_rows[0] if card_rows else None
    if not card_row:
        log.warning("group_summary_card_not_found", card_id=new_card_id)
        return None

    existing_summary = {
        "headline": existing_row["headline"],
        "summary": existing_row["summary"],
        "key_events": json.loads(existing_row["key_events"] or "[]"),
        "current_status": existing_row["current_status"],
        "pending_actions": json.loads(existing_row["pending_actions"] or "null"),
    }

    resolved_space_id = space_id or existing_row.get("space_id") or "default"

    messages = build_rolling_messages(existing_summary, dict(card_row), entity_id)
    resp = await llm_call(
        role="group_summary",
        messages=messages,
        response_schema=GROUP_SUMMARY_JSON_SCHEMA,
        card_id=new_card_id,
        step="group_summary_rolling",
        temperature=0.1,
        max_tokens=1000,
        space_id=resolved_space_id,
    )

    if not resp.parsed:
        log.warning("group_summary_rolling_parse_failed", entity_id=entity_id)
        return None

    parsed = resp.parsed
    prev_card_ids = json.loads(existing_row["card_ids"] or "[]")
    if new_card_id not in prev_card_ids:
        prev_card_ids.append(new_card_id)
    card_ids = prev_card_ids

    await db.execute(
        """UPDATE group_summaries SET
               headline = ?, summary = ?, key_events = ?,
               current_status = ?, pending_actions = ?,
               card_ids = ?, card_count = ?, model = ?,
               updated_at = datetime('now')
           WHERE entity_id = ?""",
        (
            parsed["headline"],
            parsed["summary"],
            json.dumps(parsed.get("key_events")),
            parsed.get("current_status"),
            json.dumps(parsed.get("pending_actions")),
            json.dumps(card_ids),
            len(card_ids),
            resp.model,
            entity_id,
        ),
    )
    await db.commit()

    summary_data = _build_response_dict(entity_id, parsed, card_ids)
    await manager.broadcast({
        "type": "group_summary_updated",
        "entity_id": entity_id,
        "summary": summary_data,
    })

    log.info(
        "group_summary_updated",
        entity_id=entity_id,
        mode="rolling",
        card_count=len(card_ids),
        model=resp.model,
    )
    return summary_data


def _build_response_dict(
    entity_id: str,
    parsed: dict,
    card_ids: list[str],
) -> dict:
    """Build the response dict matching GroupSummaryResponse shape."""
    return {
        "entity_id": entity_id,
        "headline": parsed["headline"],
        "summary": parsed["summary"],
        "key_events": parsed.get("key_events"),
        "current_status": parsed.get("current_status"),
        "pending_actions": parsed.get("pending_actions"),
        "card_count": len(card_ids),
        "card_ids": card_ids,
        "updated_at": None,  # Will be set by DB default
    }
