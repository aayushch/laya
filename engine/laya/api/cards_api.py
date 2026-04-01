"""Cards REST API — list, detail, approve, dismiss action cards."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.agents.session_manager import cancel_sessions_for_card
from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.models.card import CardGroup, CardResponse, CardsListResponse, GroupedCardsResponse, StagedOutput, SuggestedAction
from laya.pipeline.summarize import trigger_summary_status_update

log = structlog.get_logger()
router = APIRouter()


def _row_to_card(row) -> CardResponse:
    """Convert a SQLite Row to a CardResponse, deserializing JSON columns."""
    intelligence = None
    if row["intelligence"]:
        try:
            intelligence = json.loads(row["intelligence"])
        except json.JSONDecodeError:
            intelligence = None

    staged_output = None
    if row["staged_output"]:
        try:
            staged_output = StagedOutput(**json.loads(row["staged_output"]))
        except (json.JSONDecodeError, Exception):
            staged_output = None

    suggested_actions = None
    if row["suggested_actions"]:
        try:
            raw_actions = json.loads(row["suggested_actions"])
            suggested_actions = [SuggestedAction(**a) for a in raw_actions]
        except (json.JSONDecodeError, Exception):
            suggested_actions = None

    return CardResponse(
        card_id=row["card_id"],
        event_id=row["event_id"],
        created_at=row["created_at"],
        priority=row["priority"],
        persona=row["persona"],
        category=row["category"],
        header=row["header"],
        summary=row["summary"],
        intelligence=intelligence,
        staged_output=staged_output,
        suggested_actions=suggested_actions,
        status=row["status"],
        privacy_tier=row["privacy_tier"] or 2,
        has_workspace=bool(row["has_workspace"]),
        resolved_at=row["resolved_at"],
        user_feedback=row["user_feedback"],
        feedback_type=row["feedback_type"],
        confidence=row["confidence"],
        router_model=row["router_model"],
        stager_model=row["stager_model"],
        updated_at=row["updated_at"],
        entity_id=row["entity_id"] if "entity_id" in row.keys() else None,
        source_ref=row["source_ref"] if "source_ref" in row.keys() else None,
        source_url=row["source_url"] if "source_url" in row.keys() else None,
        selected_action_id=row["selected_action_id"] if "selected_action_id" in row.keys() else None,
        actor_name=row["actor_name"] if "actor_name" in row.keys() else None,
        actor_email=row["actor_email"] if "actor_email" in row.keys() else None,
        space_id=row["space_id"] if "space_id" in row.keys() else None,
        space_name=row["space_name"] if "space_name" in row.keys() else None,
        space_color=row["space_color"] if "space_color" in row.keys() else None,
        bookmarked_at=row["bookmarked_at"] if "bookmarked_at" in row.keys() else None,
        group_active_at=row["group_active_at"] if "group_active_at" in row.keys() else None,
    )


@router.get("/cards")
async def list_cards(
    status: str | None = None,
    priority: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "created_at_desc",
) -> CardsListResponse:
    """List action cards with optional filters and sorting."""
    db = await get_db()

    # Build WHERE clause
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("c.status = ?")
        params.append(status)
    if priority:
        conditions.append("c.priority = ?")
        params.append(priority)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Sort
    sort_map = {
        "created_at_desc": "c.created_at DESC",
        "created_at_asc": "c.created_at ASC",
        "priority_desc": """CASE c.priority
            WHEN 'CRITICAL' THEN 0
            WHEN 'HIGH' THEN 1
            WHEN 'MEDIUM' THEN 2
            WHEN 'LOW' THEN 3
            END ASC""",
    }
    order_by = sort_map.get(sort, "created_at DESC")

    # Count total
    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM action_cards c {where_clause}", params
    )
    total = count_rows[0][0]

    # Fetch page
    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                   c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                   c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                   c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                   c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                   c.space_id, c.bookmarked_at, c.group_active_at,
                   e.actor_name, e.actor_email,
                   s.name AS space_name, s.color AS space_color
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            LEFT JOIN spaces s ON c.space_id = s.space_id
            {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )

    cards = [_row_to_card(r) for r in rows]

    return CardsListResponse(cards=cards, total=total, limit=limit, offset=offset)


_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_TERMINAL = {"done", "dismissed", "failed"}


@router.get("/cards/grouped")
async def get_grouped_cards(
    status: str | None = None,
    priority: str | None = None,
    sort: str = "newest",
    sort_asc: bool = False,
    show_archived: bool = False,
    date: str | None = None,
    space_id: str | None = None,
    tz: str | None = None,
    bookmarked: bool = False,
) -> GroupedCardsResponse:
    """Return cards grouped by entity_id, filtered by date and space."""
    db = await get_db()

    conditions: list[str] = []
    params: list[Any] = []
    if bookmarked:
        conditions.append("c.bookmarked_at IS NOT NULL")
    if space_id:
        space_ids = [s.strip() for s in space_id.split(",") if s.strip()]
        if len(space_ids) == 1:
            conditions.append("c.space_id = ?")
            params.append(space_ids[0])
        elif space_ids:
            placeholders = ",".join("?" for _ in space_ids)
            conditions.append(f"c.space_id IN ({placeholders})")
            params.extend(space_ids)
    if date and not bookmarked:
        if tz:
            try:
                local_tz = ZoneInfo(tz)
                # Build midnight..next-midnight in the client's timezone, then convert to UTC
                local_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=local_tz)
                local_end = local_start + timedelta(days=1)
                utc_start = local_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                utc_end = local_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conditions.append("c.group_active_at >= ? AND c.group_active_at < ?")
                params.extend([utc_start, utc_end])
            except (KeyError, ValueError):
                # Bad timezone — fall back to plain DATE match
                conditions.append("DATE(c.group_active_at) = ?")
                params.append(date)
        else:
            conditions.append("DATE(c.group_active_at) = ?")
            params.append(date)
    if status:
        # Support comma-separated multi-select, e.g. "pending,approved"
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        # When show_archived is on, ensure 'archived' is included alongside
        # any active status filters so the Archived button always works
        if show_archived and "archived" not in statuses:
            statuses.append("archived")
        if len(statuses) == 1:
            conditions.append("c.status = ?")
            params.append(statuses[0])
        elif statuses:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"c.status IN ({placeholders})")
            params.extend(statuses)
    if priority:
        # Support comma-separated multi-select, e.g. "CRITICAL,HIGH"
        priorities = [p.strip() for p in priority.split(",") if p.strip()]
        if len(priorities) == 1:
            conditions.append("c.priority = ?")
            params.append(priorities[0])
        elif priorities:
            placeholders = ",".join("?" for _ in priorities)
            conditions.append(f"c.priority IN ({placeholders})")
            params.extend(priorities)
    if not show_archived:
        conditions.append("c.status != 'archived'")
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                   c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                   c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                   c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                   c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                   c.space_id, c.bookmarked_at, c.group_active_at,
                   e.actor_name, e.actor_email,
                   s.name AS space_name, s.color AS space_color
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            LEFT JOIN spaces s ON c.space_id = s.space_id
            {where_clause}
            ORDER BY c.created_at DESC""",
        params,
    )

    groups: dict[str, list] = {}
    for row in rows:
        eid = row["entity_id"] or f"singleton:{row['card_id']}"
        if eid not in groups:
            groups[eid] = []
        groups[eid].append(row)

    event_ids = [rows_list[0]["event_id"] for rows_list in groups.values()]
    event_meta: dict[str, dict] = {}
    if event_ids:
        placeholders = ",".join("?" * len(event_ids))
        ev_rows = await db.execute_fetchall(
            f"SELECT event_id, subject_title, subject_url, source_platform FROM events WHERE event_id IN ({placeholders})",
            event_ids,
        )
        for ev in ev_rows:
            event_meta[ev["event_id"]] = dict(ev)

    result: list[CardGroup] = []
    for entity_id, entity_rows in groups.items():
        cards = [_row_to_card(r) for r in entity_rows]
        meta = event_meta.get(entity_rows[0]["event_id"], {})
        top_priority = min(
            (c.priority for c in cards),
            key=lambda p: _PRIORITY_ORDER.get(p, 99),
        )
        latest_at = max((c.created_at or "") for c in cards)
        has_pending = any(c.status in ("pending", "ready", "requires_approval") for c in cards)
        result.append(
            CardGroup(
                entity_id=entity_id,
                entity_title=meta.get("subject_title") or entity_id,
                entity_url=meta.get("subject_url"),
                platform=meta.get("source_platform", ""),
                card_count=len(cards),
                top_priority=top_priority,
                latest_at=latest_at,
                has_pending=has_pending,
                cards=cards,
            )
        )

    # Default sort direction per type: False = descending (default), True = ascending
    # sort_asc flips the default direction for each sort type.
    if sort == "oldest":
        result.sort(key=lambda g: g.latest_at, reverse=sort_asc)
    elif sort == "priority":
        result.sort(key=lambda g: _PRIORITY_ORDER.get(g.top_priority, 99), reverse=sort_asc)
        for g in result:
            g.sort_key = g.top_priority
    elif sort == "platform":
        result.sort(key=lambda g: g.platform.lower(), reverse=sort_asc)
        for g in result:
            g.sort_key = g.platform or "Unknown"
    elif sort == "persona":
        result.sort(key=lambda g: g.cards[0].persona if g.cards else "", reverse=sort_asc)
        for g in result:
            g.sort_key = g.cards[0].persona if g.cards else "Unknown"
    elif sort == "category":
        result.sort(key=lambda g: g.cards[0].category if g.cards else "", reverse=sort_asc)
        for g in result:
            g.sort_key = g.cards[0].category if g.cards else "Unknown"
    elif sort == "status":
        _STATUS_ORDER = {
            "awaiting_input": 0, "failed": 1, "requires_approval": 2,
            "agent_running": 3, "pending": 4, "ready": 5,
            "done": 6, "dismissed": 7, "archived": 8,
        }
        _STATUS_LABEL = {
            "awaiting_input": "Input Needed", "failed": "Failed",
            "requires_approval": "Needs Approval", "agent_running": "Agent Running",
            "pending": "Processing", "ready": "Ready",
            "done": "Done", "dismissed": "Dismissed", "archived": "Archived",
        }
        def _group_status(g):
            """Pick the highest-priority status across all cards in the group."""
            for s in _STATUS_ORDER:
                if any(c.status == s for c in g.cards):
                    return s
            return g.cards[0].status if g.cards else "pending"
        result.sort(key=lambda g: _STATUS_ORDER.get(_group_status(g), 99), reverse=sort_asc)
        for g in result:
            s = _group_status(g)
            g.sort_key = _STATUS_LABEL.get(s, s.replace("_", " ").title())
    else:  # newest (default)
        result.sort(key=lambda g: g.latest_at, reverse=not sort_asc)

    # Resolve prev/next dates for pagination
    prev_date_val: str | None = None
    next_date_val: str | None = None
    if date:
        # Build a SQLite date expression that converts UTC group_active_at to local date
        date_expr = "DATE(group_active_at)"
        if tz:
            try:
                local_tz = ZoneInfo(tz)
                utc_offset_sec = int(datetime.now(local_tz).utcoffset().total_seconds())  # type: ignore[union-attr]
                date_expr = f"DATE(group_active_at, '+{utc_offset_sec} seconds')" if utc_offset_sec >= 0 else f"DATE(group_active_at, '{utc_offset_sec} seconds')"
            except (KeyError, ValueError, AttributeError):
                pass  # Use plain DATE(group_active_at)

        prev_rows = await db.execute_fetchall(
            f"SELECT {date_expr} AS d FROM action_cards WHERE {date_expr} < ? GROUP BY d ORDER BY d DESC LIMIT 1",
            (date,),
        )
        if prev_rows:
            prev_date_val = prev_rows[0]["d"]
        next_rows = await db.execute_fetchall(
            f"SELECT {date_expr} AS d FROM action_cards WHERE {date_expr} > ? GROUP BY d ORDER BY d ASC LIMIT 1",
            (date,),
        )
        if next_rows:
            next_date_val = next_rows[0]["d"]

    return GroupedCardsResponse(
        groups=result,
        total_groups=len(result),
        date=date,
        prev_date=prev_date_val,
        next_date=next_date_val,
        space_id=space_id,
    )


@router.post("/cards/group/{entity_id:path}/dismiss-all")
async def dismiss_group(entity_id: str) -> dict:
    """Dismiss all non-terminal cards in a group."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id, status FROM action_cards WHERE entity_id = ? AND status NOT IN ('done', 'dismissed', 'failed')",
        (entity_id,),
    )

    now = datetime.now(timezone.utc).isoformat()
    dismissed = 0
    for row in rows:
        await db.execute(
            "UPDATE action_cards SET status = 'dismissed', previous_status = ?, resolved_at = ?, updated_at = ? WHERE card_id = ?",
            (row["status"], now, now, row["card_id"]),
        )
        await manager.broadcast(
            {"type": "card_updated", "card_id": row["card_id"], "payload": {"status": "dismissed"}}
        )
        dismissed += 1

    await db.commit()
    log.info("group_dismissed", entity_id=entity_id, count=dismissed)
    return {"dismissed": dismissed, "entity_id": entity_id}


@router.post("/cards/{card_id}/archive")
async def archive_card(card_id: str) -> dict:
    """Archive an action card."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    current = rows[0]["status"]
    if current == "archived":
        return {"status": "archived", "card_id": card_id}

    # Cancel any running agent session before archiving (don't block the HTTP response)
    try:
        await asyncio.wait_for(cancel_sessions_for_card(card_id), timeout=2.0)
    except asyncio.TimeoutError:
        log.warning("session_cancel_timeout", card_id=card_id)

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET status = 'archived', previous_status = ?, updated_at = ? WHERE card_id = ?",
        (current, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "archived"}}
    )

    log.info("card_archived", card_id=card_id)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        asyncio.create_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "archived"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "archived", "card_id": card_id}


@router.post("/cards/{card_id}/reopen")
async def reopen_card(card_id: str) -> dict:
    """Reopen a card, retrying the last failed stage when applicable.

    Retry strategy based on failed_stage:
    - agent_spawn / agent_execution: re-queue for agent approval (requires_approval)
    - action_execution: reset to ready so user can re-execute the action
    - pipeline / NULL: reset to pending for full reprocessing
    - Archived/done/dismissed cards with previous_status: restore that status
    - Fallback: reset to pending
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status, failed_stage, agent_prompt, persona, space_id, previous_status FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    current = row["status"]
    reopenable = {"dismissed", "done", "failed", "archived", "agent_running"}
    if current not in reopenable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{current}' cannot be reopened"
        )

    now = datetime.now(timezone.utc).isoformat()
    failed_stage = row["failed_stage"] if current == "failed" else None

    if failed_stage in ("agent_spawn", "agent_execution") and row["agent_prompt"]:
        # Agent failed — put card back to requires_approval so user can click
        # "Approve Agent" again (or auto-run if in automatic mode)
        new_status = "requires_approval"
        await db.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, resolved_at = NULL, updated_at = ? WHERE card_id = ?",
            (new_status, now, card_id),
        )
        await db.commit()

        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": new_status}}
        )

        log.info("card_reopened", card_id=card_id, retry_stage=failed_stage, new_status=new_status)

    elif failed_stage == "action_execution":
        # Action execution failed — put card back to ready so user can re-execute
        new_status = "ready"
        await db.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, resolved_at = NULL, updated_at = ? WHERE card_id = ?",
            (new_status, now, card_id),
        )
        await db.commit()

        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": new_status}}
        )

        log.info("card_reopened", card_id=card_id, retry_stage=failed_stage, new_status=new_status)

    elif row["previous_status"]:
        # Card has a saved previous_status (from done/dismissed/archived) — restore it
        new_status = row["previous_status"]
        await db.execute(
            "UPDATE action_cards SET status = ?, previous_status = NULL, resolved_at = NULL, updated_at = ? WHERE card_id = ?",
            (new_status, now, card_id),
        )
        await db.commit()

        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": new_status}}
        )

        log.info("card_reopened", card_id=card_id, previous_status=row["previous_status"], new_status=new_status)

    else:
        # No previous_status saved — reset to pending for full reprocessing
        new_status = "pending"
        await db.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, resolved_at = NULL, updated_at = ? WHERE card_id = ?",
            (new_status, now, card_id),
        )
        await db.commit()

        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": new_status}}
        )

        log.info("card_reopened", card_id=card_id, retry_stage=failed_stage, new_status=new_status)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        asyncio.create_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], new_status),
            name=f"summary_status_{card_id}",
        )

    return {"status": new_status, "card_id": card_id}


@router.get("/cards/{card_id}")
async def get_card(card_id: str) -> CardResponse:
    """Get full action card detail."""
    # Normalize: chat LLM may reference cards without the "card_" prefix
    if not card_id.startswith("card_"):
        card_id = f"card_{card_id}"
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                  c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                  c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                  c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                  c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                  c.space_id, c.bookmarked_at, c.group_active_at,
                  e.actor_name, e.actor_email,
                  s.name AS space_name, s.color AS space_color
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           LEFT JOIN spaces s ON c.space_id = s.space_id
           WHERE c.card_id = ?""",
        (card_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    return _row_to_card(rows[0])


@router.post("/cards/{card_id}/done")
async def mark_card_done(card_id: str) -> dict:
    """Mark an action card as done (user has reviewed/acted on it)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    doneable = {"pending", "ready", "requires_approval", "awaiting_input"}
    if rows[0]["status"] not in doneable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' cannot be marked done"
        )

    current = rows[0]["status"]
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE action_cards
           SET status = 'done', previous_status = ?, resolved_at = ?, updated_at = ?
           WHERE card_id = ?""",
        (current, now, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "done"}}
    )

    log.info("card_done", card_id=card_id)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        asyncio.create_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "done"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "done", "card_id": card_id}


@router.post("/cards/{card_id}/approve-agent")
async def approve_agent(card_id: str) -> dict:
    """Approve agent execution for a requires_approval card.

    Retrieves the stored agent prompt, spawns the coding agent in the
    background, and transitions the card to agent_running.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status, event_id, agent_prompt, persona, space_id FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    if row["status"] != "requires_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Card status '{row['status']}' is not requires_approval",
        )

    if not row["agent_prompt"]:
        raise HTTPException(
            status_code=409, detail="No agent prompt stored for this card"
        )

    # Transition to agent_running immediately
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET status = 'agent_running', updated_at = ? WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "agent_running"}}
    )

    # Spawn agent in background
    from laya.workers.engineer import run_engineer_from_prompt

    asyncio.create_task(
        run_engineer_from_prompt(
            card_id=card_id,
            agent_prompt=row["agent_prompt"],
            space_id=row["space_id"],
        ),
        name=f"agent_{card_id}",
    )

    log.info("agent_approved", card_id=card_id)
    return {"status": "agent_running", "card_id": card_id}


async def _delete_card_cascade(db, card_id: str, event_id: str | None) -> None:
    """Hard-delete a card and all its related rows in correct FK order."""
    # workspace_events → workspace_sessions → action_log → audit_log → action_cards → events
    await db.execute(
        "DELETE FROM workspace_events WHERE session_id IN "
        "(SELECT session_id FROM workspace_sessions WHERE card_id = ?)",
        (card_id,),
    )
    await db.execute("DELETE FROM workspace_sessions WHERE card_id = ?", (card_id,))
    await db.execute("DELETE FROM action_log WHERE card_id = ?", (card_id,))
    await db.execute("DELETE FROM audit_log WHERE card_id = ?", (card_id,))
    await db.execute("DELETE FROM action_cards WHERE card_id = ?", (card_id,))
    # Remove the source event only if no other cards still reference it
    if event_id:
        await db.execute(
            "DELETE FROM events WHERE event_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM action_cards WHERE event_id = ?)",
            (event_id, event_id),
        )


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str) -> dict:
    """Permanently delete an archived card and all related data."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status, event_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    if rows[0]["status"] != "archived":
        raise HTTPException(
            status_code=409, detail="Only archived cards can be deleted"
        )

    # Cancel any lingering agent session (don't block the HTTP response)
    try:
        await asyncio.wait_for(cancel_sessions_for_card(card_id), timeout=2.0)
    except asyncio.TimeoutError:
        log.warning("session_cancel_timeout_on_delete", card_id=card_id)

    event_id = rows[0]["event_id"]
    await _delete_card_cascade(db, card_id, event_id)
    await db.commit()

    # Remove card embedding from ChromaDB (best-effort, with timeout)
    try:
        from laya.db.chromadb_store import delete_document
        await asyncio.wait_for(delete_document(f"card_{card_id}"), timeout=3.0)
    except Exception as e:
        log.warning("card_embed_delete_failed", card_id=card_id, error=str(e))

    await manager.broadcast({"type": "card_deleted", "card_id": card_id})
    log.info("card_deleted", card_id=card_id)
    return {"status": "deleted", "card_id": card_id}


@router.post("/cards/{card_id}/bookmark")
async def bookmark_card(card_id: str) -> dict:
    """Bookmark a card for later."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET bookmarked_at = ? WHERE card_id = ?",
        (now, card_id),
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


class DismissRequest(BaseModel):
    reason: str | None = None
    feedback_type: str | None = None


@router.post("/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str, body: DismissRequest | None = None) -> dict:
    """Dismiss an action card with optional feedback."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    terminal = {"done", "failed", "dismissed"}
    if rows[0]["status"] in terminal:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' is terminal"
        )

    current = rows[0]["status"]
    now = datetime.now(timezone.utc).isoformat()
    reason = body.reason if body else None
    feedback_type = body.feedback_type if body else None

    await db.execute(
        """UPDATE action_cards
           SET status = 'dismissed', previous_status = ?, resolved_at = ?, user_feedback = ?,
               feedback_type = ?, updated_at = ?
           WHERE card_id = ?""",
        (current, now, reason, feedback_type, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "dismissed"}}
    )

    log.info("card_dismissed", card_id=card_id, feedback_type=feedback_type)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        asyncio.create_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "dismissed"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "dismissed", "card_id": card_id}


class UpdateClassificationRequest(BaseModel):
    priority: str | None = None
    persona: str | None = None
    rule_text: str | None = None


@router.patch("/cards/{card_id}/classification")
async def update_classification(card_id: str, body: UpdateClassificationRequest) -> dict:
    """Update a card's priority/persona and log corrections for the learning loop."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.priority, ac.persona, ac.category, ac.summary,
                  ac.space_id, e.source_platform, e.source_raw_event_type
           FROM action_cards ac
           LEFT JOIN events e ON ac.event_id = e.event_id
           WHERE ac.card_id = ?""",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    card = rows[0]
    now = datetime.now(timezone.utc).isoformat()
    valid_priorities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    valid_personas = {"ENGINEER", "COMMS", "OPS"}

    if body.priority and body.priority not in valid_priorities:
        raise HTTPException(status_code=422, detail=f"Invalid priority: {body.priority}")
    if body.persona and body.persona not in valid_personas:
        raise HTTPException(status_code=422, detail=f"Invalid persona: {body.persona}")

    # Log corrections for changed fields
    corrections = []
    if body.priority and body.priority != card["priority"]:
        corrections.append(("priority", card["priority"], body.priority))
    if body.persona and body.persona != card["persona"]:
        corrections.append(("persona", card["persona"], body.persona))

    if not corrections and not body.rule_text:
        return {"status": "no_changes", "card_id": card_id}

    # Insert correction records
    for field, original, corrected in corrections:
        await db.execute(
            """INSERT INTO classification_corrections
               (card_id, space_id, field, original_value, corrected_value,
                card_summary, category, platform, event_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id, card["space_id"], field, original, corrected,
                card["summary"], card["category"],
                card["source_platform"], card["source_raw_event_type"],
            ),
        )

    # Update the card itself
    update_parts = []
    update_params: list[Any] = []
    if body.priority and body.priority != card["priority"]:
        update_parts.append("priority = ?")
        update_params.append(body.priority)
    if body.persona and body.persona != card["persona"]:
        update_parts.append("persona = ?")
        update_params.append(body.persona)

    if update_parts:
        update_parts.append("updated_at = ?")
        update_params.append(now)
        update_params.append(card_id)
        await db.execute(
            f"UPDATE action_cards SET {', '.join(update_parts)} WHERE card_id = ?",
            tuple(update_params),
        )

    # Create classification rule if provided
    if body.rule_text:
        await db.execute(
            """INSERT INTO classification_rules (space_id, field, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, ?, 'manual', 1, ?, ?)""",
            (card["space_id"], corrections[0][0] if corrections else None, body.rule_text, now, now),
        )

    await db.commit()

    # Broadcast update
    payload: dict[str, Any] = {}
    if body.priority and body.priority != card["priority"]:
        payload["priority"] = body.priority
    if body.persona and body.persona != card["persona"]:
        payload["persona"] = body.persona
    if payload:
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": payload}
        )

    log.info(
        "classification_updated",
        card_id=card_id,
        corrections=[(c[0], c[1], c[2]) for c in corrections],
        rule_added=bool(body.rule_text),
    )
    return {"status": "updated", "card_id": card_id, "corrections": len(corrections)}


@router.get("/summary")
async def get_daily_summary(date: str | None = None) -> dict:
    """Get the daily summary for a given date (defaults to today)."""
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT summary_json, card_ids, updated_at FROM daily_summaries WHERE date = ?",
        (date,),
    )

    if not rows:
        return {
            "date": date,
            "summary": None,
            "card_ids": [],
            "updated_at": None,
        }

    try:
        summary = json.loads(rows[0]["summary_json"])
        card_ids = json.loads(rows[0]["card_ids"])
    except json.JSONDecodeError:
        summary = None
        card_ids = []

    return {
        "date": date,
        "summary": summary,
        "card_ids": card_ids,
        "updated_at": rows[0]["updated_at"],
    }
