# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Group summary pipeline — rolling LLM summaries for entity-id card groups."""

from __future__ import annotations

import asyncio
import json

import structlog

from laya.api.websocket import manager
from laya.config import get_debounce_config, load_settings
from laya.db.sqlite import get_db
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.group_summary import (
    GROUP_SUMMARY_JSON_SCHEMA,
    build_context_summary_messages,
    build_initial_messages,
    build_rolling_messages,
)

log = structlog.get_logger()

# Debounce state: accumulate card_ids per entity and batch after quiet period.
_debounce_lock = asyncio.Lock()
_pending_updates: dict[str, list[str]] = {}  # entity_id → [card_ids]
_pending_space: dict[str, str | None] = {}  # entity_id → space_id
_debounce_tasks: dict[str, asyncio.Task] = {}  # entity_id → timer task

# Per-entity run locks. A new debounce batch can be scheduled while the previous
# batch is still mid-LLM (the timer task removes itself from _debounce_tasks
# before running), so two _run_group_summary calls for the same entity could
# otherwise overlap and lost-update the forward-only rolling summary — dropping
# cards from it permanently (review §2 pipeline / §3.4).
_entity_run_locks: dict[str, asyncio.Lock] = {}


def _get_run_lock(key: str) -> asyncio.Lock:
    # Safe without a guard: creation is synchronous (no await between get/set).
    lock = _entity_run_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _entity_run_locks[key] = lock
    return lock


async def trigger_group_summary_update(
    entity_id: str,
    new_card_id: str,
    space_id: str | None = None,
) -> None:
    """Queue a group summary update (debounced per entity).

    Multiple cards arriving for the same entity within the debounce window
    are batched into a single LLM call.
    """
    settings = load_settings()
    if not settings.get("group_summaries", {}).get("enabled", True):
        return

    cfg = get_debounce_config()
    debounce_seconds = cfg.get("group_summary_seconds", 15)

    if debounce_seconds <= 0:
        # Debounce disabled — run immediately (backwards compat)
        async with _get_run_lock(entity_id):
            await _run_group_summary(entity_id, [new_card_id], space_id)
        return

    async with _debounce_lock:
        if entity_id not in _pending_updates:
            _pending_updates[entity_id] = []
        _pending_updates[entity_id].append(new_card_id)
        _pending_space[entity_id] = space_id

        # Cancel existing timer for this entity and restart
        if entity_id in _debounce_tasks and not _debounce_tasks[entity_id].done():
            _debounce_tasks[entity_id].cancel()

        from laya.tasks import create_task
        _debounce_tasks[entity_id] = create_task(
            _debounced_group_summary(entity_id, debounce_seconds),
            name=f"group_summary_debounce_{entity_id}",
        )


async def _debounced_group_summary(entity_id: str, delay: float) -> None:
    """Wait for quiet period, then process all pending cards for this entity."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return  # Another card arrived, timer restarted

    async with _debounce_lock:
        card_ids = _pending_updates.pop(entity_id, [])
        space_id = _pending_space.pop(entity_id, None)
        _debounce_tasks.pop(entity_id, None)

    if not card_ids:
        return

    # Serialize per-entity so a batch scheduled while this one is mid-LLM can't
    # lost-update the rolling summary (review §2 pipeline / §3.4).
    async with _get_run_lock(entity_id):
        await _run_group_summary(entity_id, card_ids, space_id)


async def _run_group_summary(
    entity_id: str,
    new_card_ids: list[str],
    space_id: str | None,
) -> None:
    """Execute the group summary generation/update for an entity or context group."""
    db = await get_db()

    try:
        # Context groups always do full generation from entity summaries
        if entity_id.startswith("ctx_"):
            count_rows = await db.execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM action_cards WHERE context_id = ?",
                (entity_id,),
            )
            if count_rows and count_rows[0]["cnt"] >= 2:
                await _generate_context_summary(db, entity_id, space_id)
            return

        # Count sibling cards (including the new ones)
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
            await _rolling_update(db, entity_id, new_card_ids, existing_row, space_id)
        else:
            await _initial_generation(db, entity_id, space_id)

        # Refresh CONTEXT.md if this entity already has one (i.e. user has
        # previously started an agent session on it). Keeps the file fresh
        # so the agent has up-to-date info on the next resume — avoids the
        # case where a card arrives after a session completed and the user
        # has to manually re-explain context. Skips when no CONTEXT.md
        # exists, so first-card entities pay nothing.
        try:
            from laya.agents.entity_context import refresh_entity_context_if_exists
            await refresh_entity_context_if_exists(entity_id, space_id)
        except Exception:
            log.exception("entity_context_refresh_failed", entity_id=entity_id)

    except Exception:
        log.exception(
            "group_summary_failed", entity_id=entity_id, card_ids=new_card_ids
        )


async def regenerate_group_summary(entity_id: str) -> dict | None:
    """Force full regeneration of a group summary from all cards."""
    db = await get_db()
    if entity_id.startswith("ctx_"):
        rows = await db.execute_fetchall(
            "SELECT space_id FROM action_cards WHERE context_id = ? LIMIT 1",
            (entity_id,),
        )
        space_id = rows[0]["space_id"] if rows else "default"
        return await _generate_context_summary(db, entity_id, space_id)
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

    # Enrich cards with tags for the LLM prompt
    from laya.pipeline.tags import batch_load_tags
    card_ids_for_tags = [c["card_id"] for c in cards]
    tags_map = await batch_load_tags(card_ids_for_tags)
    for c in cards:
        card_tag_entries = tags_map.get(("card", c["card_id"]), [])
        c["tags"] = ", ".join(t["tag_name"] for t in card_tag_entries) if card_tag_entries else ""

    resolved_space_id = space_id or cards[0].get("space_id") or "default"

    messages = build_initial_messages(cards, entity_id)
    resp = await llm_call(
        role="group_summary",
        messages=messages,
        response_schema=GROUP_SUMMARY_JSON_SCHEMA,
        step="group_summary_initial",
        temperature=0.1,
        max_tokens=DEFAULT_MAX_TOKENS,
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

    await _cascade_to_context_group(db, entity_id, card_ids, resolved_space_id)
    return summary_data


async def _rolling_update(
    db,
    entity_id: str,
    new_card_ids: list[str],
    existing_row: dict,
    space_id: str | None,
) -> dict | None:
    """Update an existing summary with one or more new cards."""
    placeholders = ",".join("?" for _ in new_card_ids)
    card_rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.header, c.summary, c.intelligence, c.status,
                   c.created_at, c.entity_id,
                   e.actor_name
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            WHERE c.card_id IN ({placeholders})
            ORDER BY c.created_at ASC""",
        tuple(new_card_ids),
    )
    if not card_rows:
        log.warning("group_summary_cards_not_found", card_ids=new_card_ids)
        return None

    new_cards = [dict(r) for r in card_rows]

    # Enrich new cards with tags for the LLM prompt
    from laya.pipeline.tags import batch_load_tags
    tags_map = await batch_load_tags(new_card_ids)
    for c in new_cards:
        card_tag_entries = tags_map.get(("card", c["card_id"]), [])
        c["tags"] = ", ".join(t["tag_name"] for t in card_tag_entries) if card_tag_entries else ""

    existing_summary = {
        "headline": existing_row["headline"],
        "summary": existing_row["summary"],
        "key_events": json.loads(existing_row["key_events"] or "[]"),
        "current_status": existing_row["current_status"],
        "pending_actions": json.loads(existing_row["pending_actions"] or "null"),
    }

    resolved_space_id = space_id or existing_row.get("space_id") or "default"

    messages = build_rolling_messages(existing_summary, new_cards, entity_id)
    resp = await llm_call(
        role="group_summary",
        messages=messages,
        response_schema=GROUP_SUMMARY_JSON_SCHEMA,
        card_id=new_card_ids[-1],
        step="group_summary_rolling",
        temperature=0.1,
        max_tokens=DEFAULT_MAX_TOKENS,
        space_id=resolved_space_id,
    )

    if not resp.parsed:
        log.warning("group_summary_rolling_parse_failed", entity_id=entity_id)
        return None

    parsed = resp.parsed
    prev_card_ids = json.loads(existing_row["card_ids"] or "[]")
    for cid in new_card_ids:
        if cid not in prev_card_ids:
            prev_card_ids.append(cid)
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
        new_cards_batched=len(new_card_ids),
        model=resp.model,
    )

    # Skip the context-group cascade when this entity's summary headline+status
    # came back identical — the context summary is derived from its member
    # entities' headline+status, so an unchanged entity can't move it. Avoids a
    # redundant nested LLM regen on every no-op rolling update (review §3.5 — P6-5).
    if (
        parsed["headline"] == existing_summary["headline"]
        and parsed.get("current_status") == existing_summary["current_status"]
    ):
        log.debug("context_cascade_skipped_unchanged", entity_id=entity_id)
    else:
        await _cascade_to_context_group(db, entity_id, card_ids, resolved_space_id)
    return summary_data


async def _cascade_to_context_group(
    db,
    entity_id: str,
    card_ids: list[str],
    space_id: str | None,
) -> None:
    """After an entity summary is saved, trigger context group summary if applicable."""
    if entity_id.startswith("ctx_"):
        return
    try:
        ctx_rows = await db.execute_fetchall(
            "SELECT DISTINCT context_id FROM action_cards WHERE entity_id = ? AND context_id IS NOT NULL LIMIT 1",
            (entity_id,),
        )
        if ctx_rows:
            context_id = ctx_rows[0]["context_id"]
            from laya.tasks import create_task
            create_task(
                trigger_group_summary_update(context_id, card_ids[-1], space_id),
                name=f"group_summary_{context_id}",
            )
    except Exception:
        log.debug("context_group_cascade_failed", entity_id=entity_id)


async def _generate_context_summary(
    db,
    context_id: str,
    space_id: str | None,
) -> dict | None:
    """Generate a context group summary from entity-level summaries."""
    # Fetch distinct entity_ids in this context group
    entity_rows = await db.execute_fetchall(
        "SELECT DISTINCT entity_id FROM action_cards WHERE context_id = ? AND entity_id IS NOT NULL",
        (context_id,),
    )
    if not entity_rows:
        return None

    distinct_eids = [r["entity_id"] for r in entity_rows]

    # Batch-fetch entity summaries
    placeholders = ",".join("?" for _ in distinct_eids)
    summary_rows = await db.execute_fetchall(
        f"SELECT * FROM group_summaries WHERE entity_id IN ({placeholders})",
        distinct_eids,
    )
    summary_by_eid = {r["entity_id"]: dict(r) for r in summary_rows}

    # Build inputs: entity summaries where available, raw cards as fallback
    entity_summaries: list[tuple[str, dict]] = []
    fallback_cards: list[dict] = []

    for eid in distinct_eids:
        if eid in summary_by_eid:
            s = summary_by_eid[eid]
            entity_summaries.append((eid, {
                "headline": s["headline"],
                "summary": s["summary"],
                "current_status": s["current_status"],
                "pending_actions": json.loads(s["pending_actions"] or "null"),
            }))
        else:
            # Fallback: fetch raw cards for this entity
            card_rows = await db.execute_fetchall(
                """SELECT c.card_id, c.header, c.summary, c.intelligence, c.status,
                          c.created_at, c.entity_id,
                          e.actor_name
                   FROM action_cards c
                   LEFT JOIN events e ON c.event_id = e.event_id
                   WHERE c.entity_id = ? AND c.context_id = ?
                   ORDER BY c.created_at ASC""",
                (eid, context_id),
            )
            fallback_cards.extend(dict(r) for r in card_rows)

    if not entity_summaries and not fallback_cards:
        return None

    # Fetch context label
    label_rows = await db.execute_fetchall(
        "SELECT label FROM context_groups WHERE context_id = ?",
        (context_id,),
    )
    context_label = label_rows[0]["label"] if label_rows and label_rows[0]["label"] else None

    resolved_space_id = space_id or "default"

    messages = build_context_summary_messages(entity_summaries, fallback_cards, context_label)
    resp = await llm_call(
        role="group_summary",
        messages=messages,
        response_schema=GROUP_SUMMARY_JSON_SCHEMA,
        step="context_summary",
        temperature=0.1,
        max_tokens=DEFAULT_MAX_TOKENS,
        space_id=resolved_space_id,
    )

    if not resp.parsed:
        log.warning("context_summary_parse_failed", context_id=context_id)
        return None

    parsed = resp.parsed
    # Collect all card_ids across all entities in the context group
    all_card_rows = await db.execute_fetchall(
        "SELECT card_id FROM action_cards WHERE context_id = ?",
        (context_id,),
    )
    card_ids = [r["card_id"] for r in all_card_rows]

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
            context_id,
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

    summary_data = _build_response_dict(context_id, parsed, card_ids)
    await manager.broadcast({
        "type": "group_summary_updated",
        "entity_id": context_id,
        "summary": summary_data,
    })

    log.info(
        "context_summary_generated",
        context_id=context_id,
        entity_count=len(distinct_eids),
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
