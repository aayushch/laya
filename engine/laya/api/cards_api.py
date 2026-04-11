"""Cards REST API — list, detail, approve, dismiss action cards."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from laya.agents.session_manager import cancel_sessions_for_card
from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.llm.client import log_to_audit
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
        context_id=row["context_id"] if "context_id" in row.keys() else None,
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
                   c.space_id, c.bookmarked_at, c.group_active_at, c.context_id,
                   e.actor_name, e.actor_email,
                   s.name AS space_name, s.color AS space_color
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            LEFT JOIN spaces s ON c.space_id = s.space_id
            {where_clause}
            ORDER BY c.created_at DESC""",
        params,
    )

    # Determine grouping mode: context_id (smart grouping) vs entity_id
    from laya.config import load_settings
    settings = load_settings()
    smart_grouping = settings.get("smart_grouping", {}).get("smart_display", True)

    groups: dict[str, list] = {}
    for row in rows:
        if smart_grouping:
            group_key = row["context_id"] or row["entity_id"] or f"singleton:{row['card_id']}"
        else:
            group_key = row["entity_id"] or f"singleton:{row['card_id']}"
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(row)

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

    # Pre-fetch context group labels for smart grouping
    context_labels: dict[str, str] = {}
    if smart_grouping:
        context_ids = [k for k in groups if k.startswith("ctx_")]
        if context_ids:
            placeholders = ",".join("?" * len(context_ids))
            ctx_rows = await db.execute_fetchall(
                f"SELECT context_id, label FROM context_groups WHERE context_id IN ({placeholders})",
                context_ids,
            )
            for cr in ctx_rows:
                if cr["label"]:
                    context_labels[cr["context_id"]] = cr["label"]

    result: list[CardGroup] = []
    for group_key, entity_rows in groups.items():
        cards = [_row_to_card(r) for r in entity_rows]
        meta = event_meta.get(entity_rows[0]["event_id"], {})
        top_priority = min(
            (c.priority for c in cards),
            key=lambda p: _PRIORITY_ORDER.get(p, 99),
        )
        latest_at = max((c.created_at or "") for c in cards)
        has_pending = any(c.status in ("pending", "ready", "requires_approval") for c in cards)

        # Resolve context group metadata
        is_context_group = group_key.startswith("ctx_")
        context_id = group_key if is_context_group else None
        context_label = context_labels.get(group_key) if is_context_group else None

        # For context groups, entity_id is the first card's entity_id;
        # entity_title uses context_label when available
        entity_id_val = entity_rows[0]["entity_id"] or group_key
        entity_title = context_label or meta.get("subject_title") or entity_id_val

        result.append(
            CardGroup(
                entity_id=entity_id_val,
                entity_title=entity_title,
                entity_url=meta.get("subject_url"),
                platform=meta.get("source_platform", ""),
                card_count=len(cards),
                top_priority=top_priority,
                latest_at=latest_at,
                has_pending=has_pending,
                cards=cards,
                context_id=context_id,
                context_label=context_label,
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
    elif sort == "actor":
        result.sort(
            key=lambda g: (g.cards[0].actor_name or "").lower() if g.cards else "",
            reverse=sort_asc,
        )
        for g in result:
            g.sort_key = (g.cards[0].actor_name if g.cards and g.cards[0].actor_name else "Unknown")
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

    # Update summary for each dismissed card so items get strikethrough
    header_rows = await db.execute_fetchall(
        "SELECT card_id, header FROM action_cards WHERE entity_id = ? AND status = 'dismissed'",
        (entity_id,),
    )
    for hr in header_rows:
        asyncio.create_task(
            trigger_summary_status_update(hr["card_id"], hr["header"], "dismissed"),
            name=f"summary_status_{hr['card_id']}",
        )

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

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "archive", "previous_status": current},
    )

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

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "reopen", "previous_status": current,
                  "new_status": new_status, "failed_stage": failed_stage},
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

    await log_to_audit(
        event_id=row["event_id"], card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "approve_agent", "persona": row["persona"]},
    )

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

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "dismiss", "previous_status": current,
                  "feedback_type": feedback_type},
    )

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


class UpdateActionPayloadRequest(BaseModel):
    action_id: str
    payload: dict[str, Any]


@router.post("/cards/{card_id}/action-payload")
async def update_action_payload(card_id: str, body: UpdateActionPayloadRequest) -> dict:
    """Update a suggested action's payload (e.g. user edits an email draft)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT suggested_actions FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    actions = json.loads(row["suggested_actions"]) if row["suggested_actions"] else []
    found = False
    for action in actions:
        if action.get("action_id") == body.action_id:
            action["payload"].update(body.payload)
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Action not found")

    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    return {"status": "updated", "card_id": card_id, "action_id": body.action_id}


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


class RunAgentRequest(BaseModel):
    prompt: str
    directory: str
    add_dirs: list[str] | None = None
    agent_type: str | None = None  # claude_code, gemini_cli, codex_cli
    mode: str | None = None  # e.g. plan, acceptEdits (claude), read-only, full-auto (codex)
    space_id: str | None = None
    images: list[str] | None = None  # Absolute paths to uploaded images


class StartResearchRequest(BaseModel):
    prompt: str | None = None  # Optional user focus question
    directory: str | None = None  # Optional working dir (defaults to ~/.laya/tmp/research/<card_id>/)


async def _stream_agent_to_card(
    card_id: str,
    prompt: str,
    directory: str,
    agent_type: "Any",
    space_id: str | None = None,
    add_dirs: list[str] | None = None,
    mode: str | None = None,
    research: bool = False,
) -> None:
    """Shared background task: spawn agent, stream events, update card status.

    Used by both run_agent() and start_research() endpoints. Handles the
    full lifecycle: session spawn, event streaming, status transitions,
    findings extraction, and session completion.
    """
    from laya.agents import session_manager
    from laya.models.workspace import SessionStatus

    try:
        session_id, agent = await session_manager.start_session(
            card_id=card_id,
            prompt=prompt,
            repo_path=directory,
            agent_type=agent_type,
            space_id=space_id,
            add_dirs=add_dirs,
            mode=mode,
            research=research,
        )
    except Exception as e:
        log.error("agent_spawn_failed", card_id=card_id, error=str(e))
        db2 = await get_db()
        await db2.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_spawn', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db2.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Broadcast workspace availability
    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"has_workspace": True, "session_id": session_id}}
    )

    # Stream events
    findings: dict[str, Any] = {}
    cc_session_id_stored = False

    try:
        async for ws_event in agent.stream_events():
            inserted = await session_manager.store_workspace_event(ws_event)
            if not inserted:
                continue

            if not cc_session_id_stored and hasattr(agent, "cc_session_id") and agent.cc_session_id:
                await session_manager.store_cc_session_id(session_id, agent.cc_session_id)
                cc_session_id_stored = True

            if ws_event.event_type.value == "approval_request":
                if ws_event.content.get("ask_user_question"):
                    db3 = await get_db()
                    await db3.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (card_id,),
                    )
                    await db3.commit()
                    await manager.broadcast(
                        {"type": "card_updated", "card_id": card_id, "payload": {"status": "awaiting_input"}}
                    )
                await manager.broadcast(
                    {"type": "approval_request", "card_id": card_id, "session_id": session_id, "payload": ws_event.content}
                )
            elif ws_event.event_type.value == "error":
                findings["last_error"] = ws_event.content.get("error", "")
                await manager.broadcast(
                    {"type": "agent_error", "card_id": card_id, "session_id": session_id, "payload": ws_event.content}
                )

            if ws_event.event_type.value == "agent_message" and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")
            if ws_event.event_type.value == "status_change":
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error("agent_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        db4 = await get_db()
        await db4.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db4.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Complete session and update card status
    final_status = agent.get_status()
    db5 = await get_db()

    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

        agent_plan = findings.get("agent_plan", "")
        agent_result = findings.get("agent_result", "")
        staged_content = agent_plan or agent_result
        if staged_content:
            staged_type = "agent_plan" if agent_plan else "agent_result"
            await db5.execute(
                "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (json.dumps({"type": staged_type, "content": staged_content}), card_id),
            )

        has_unanswered = await session_manager.has_unanswered_questions(session_id)
        card_status = "awaiting_input" if has_unanswered else "ready"

        await db5.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_status, card_id),
        )
        await db5.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": card_status}}
        )
        await manager.broadcast(
            {"type": "agent_completed", "card_id": card_id, "session_id": session_id, "payload": {"findings": findings}}
        )
    elif final_status == SessionStatus.CANCELLED:
        await session_manager.complete_session(session_id, error="Cancelled by user")
        await db5.execute(
            "UPDATE action_cards SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db5.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "ready"}}
        )
    else:
        last_error = findings.get("last_error", "")
        error_msg = f"Agent ended with status: {final_status.value}"
        if last_error:
            error_msg += f" — {last_error}"
        log.error("agent_failed", session_id=session_id, error=error_msg)
        await session_manager.complete_session(session_id, error=error_msg)
        await db5.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db5.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )


@router.post("/upload-agent-image")
async def upload_agent_image(file: UploadFile = File(...)) -> dict:
    """Upload an image for use with an agent run.

    Saves the file to ~/.laya/tmp/agent-images/<uuid>.<ext> and returns the
    absolute path so the frontend can collect paths before submitting the
    run-agent request.
    """
    from laya.config import LAYA_HOME

    images_dir = LAYA_HOME / "tmp" / "agent-images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Determine extension from filename or content type
    ext = "png"
    if file.filename:
        parts = file.filename.rsplit(".", 1)
        if len(parts) == 2 and parts[1].lower() in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"):
            ext = parts[1].lower()
    elif file.content_type:
        ct_map = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
        ext = ct_map.get(file.content_type, "png")

    import uuid as _uuid
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    filepath = images_dir / filename

    content = await file.read()
    filepath.write_bytes(content)

    log.info("agent_image_uploaded", path=str(filepath), size=len(content))
    return {"path": str(filepath), "filename": filename, "size": len(content)}


@router.post("/cards/run-agent")
async def run_agent(body: RunAgentRequest) -> dict:
    """Create an ENGINEER card and spawn a coding agent directly.

    User-initiated agent run (triggered from the 'a' keyboard shortcut).
    Creates a card with source=laya, persona=ENGINEER, and immediately
    spawns the agent subprocess. The card then follows the normal workspace flow.
    """
    from laya.agents import session_manager
    from laya.models.workspace import AgentType

    # Resolve agent type
    if body.agent_type:
        try:
            agent_type = AgentType(body.agent_type)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown agent type: {body.agent_type}")
    else:
        agent_type = session_manager.get_configured_agent_type()

    # Build the effective prompt — append image references so the agent
    # can read them via its Read/file tool (agents don't have --image flags)
    effective_prompt = body.prompt
    if body.images:
        image_lines = "\n".join(f"- {p}" for p in body.images)
        effective_prompt += (
            f"\n\nAttached reference images (use your Read/file tool to view them):\n{image_lines}"
        )

    # Create synthetic event + card
    import uuid
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    header = body.prompt[:120] + ("..." if len(body.prompt) > 120 else "")
    entity_id = f"laya:agent_run:{card_id}"

    db = await get_db()

    # Synthetic event so the FK constraint is satisfied
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, content_body, raw_json, processed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            now,
            "laya",
            "agent_run",
            "agent_task",
            card_id,
            header,
            body.prompt,
            json.dumps({"source": "laya", "type": "agent_run", "prompt": body.prompt}),
            True,
        ),
    )

    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            intelligence, staged_output, suggested_actions, status,
            privacy_tier, has_workspace, confidence, entity_id, source_ref,
            space_id, agent_prompt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            event_id,
            "MEDIUM",
            "ENGINEER",
            "CODE",
            header,
            body.prompt,  # summary shows the original prompt (without image paths)
            json.dumps({}),
            json.dumps({}),
            json.dumps([]),
            "agent_running",
            "internal",
            True,
            1.0,
            entity_id,
            "Agent Run",
            body.space_id or "default",
            effective_prompt,  # agent_prompt includes image references
        ),
    )
    await db.commit()

    # Broadcast card creation
    await manager.broadcast(
        {
            "type": "card_created",
            "card_id": card_id,
            "payload": {
                "header": header,
                "summary": body.prompt,
                "priority": "MEDIUM",
                "persona": "ENGINEER",
                "category": "CODE",
                "status": "agent_running",
                "has_workspace": True,
                "privacy_tier": "internal",
            },
        }
    )

    # Spawn agent in background
    async def _run_agent_task() -> None:
        await _stream_agent_to_card(
            card_id=card_id,
            prompt=effective_prompt,
            directory=body.directory,
            agent_type=agent_type,
            space_id=body.space_id,
            add_dirs=body.add_dirs,
            mode=body.mode,
        )
        # Clean up uploaded images after agent finishes (regardless of outcome)
        if body.images:
            for img_path in body.images:
                try:
                    from pathlib import Path
                    p = Path(img_path)
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception as e:
                    log.debug("agent_image_cleanup_failed", path=img_path, error=str(e))

    asyncio.create_task(_run_agent_task(), name=f"run_agent_{card_id}")

    log.info("run_agent_initiated", card_id=card_id, agent_type=agent_type.value)
    return {"status": "agent_running", "card_id": card_id}


@router.post("/cards/{card_id}/start-research")
async def start_research(card_id: str, body: StartResearchRequest) -> dict:
    """Start a research agent session on an existing card.

    Unlike run-agent (which creates a new card), this attaches a workspace
    to an existing card of any persona. Builds a research-oriented prompt
    from the card's context and spawns the configured agent.
    """
    from laya.agents import session_manager
    from laya.config import LAYA_HOME, load_settings
    from laya.llm.prompts.research import build_research_prompt

    db = await get_db()

    # Fetch card
    rows = await db.execute_fetchall(
        "SELECT card_id, event_id, status, header, summary, intelligence, space_id FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    card = rows[0]

    # Validate status — reject statuses where research doesn't make sense
    blocked = {"agent_running", "awaiting_input", "archived", "pending"}
    if card["status"] in blocked:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start research on card with status '{card['status']}'"
        )

    # Validate agent is configured
    settings = load_settings()
    agent_setting = settings.get("coding_agent", "none")
    if agent_setting == "none":
        raise HTTPException(
            status_code=409,
            detail="No agent configured. Set a coding agent in Settings to use research."
        )

    agent_type = session_manager.get_configured_agent_type()

    # Fetch source event body for context
    event_body = ""
    platform = "unknown"
    event_rows = await db.execute_fetchall(
        "SELECT content_body, source_platform FROM events WHERE event_id = ?",
        (card["event_id"],),
    )
    if event_rows:
        event_body = event_rows[0]["content_body"] or ""
        platform = event_rows[0]["source_platform"] or "unknown"

    # Parse intelligence
    intelligence: list[str] = []
    if card["intelligence"]:
        try:
            parsed = json.loads(card["intelligence"])
            if isinstance(parsed, list):
                intelligence = parsed
        except json.JSONDecodeError:
            pass

    # Build research prompt
    research_prompt = build_research_prompt(
        header=card["header"] or "",
        summary=card["summary"] or "",
        intelligence=intelligence,
        event_body=event_body,
        platform=platform,
        user_question=body.prompt,
    )

    # Resolve working directory
    if body.directory:
        directory = body.directory
    else:
        research_dir = LAYA_HOME / "tmp" / "research" / card_id
        research_dir.mkdir(parents=True, exist_ok=True)
        directory = str(research_dir)

    # Update card: set status, workspace flag, and store the research prompt
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE action_cards
           SET status = 'agent_running', has_workspace = 1, agent_prompt = ?, updated_at = ?
           WHERE card_id = ?""",
        (research_prompt, now, card_id),
    )
    await db.commit()

    # Broadcast status change
    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "agent_running", "has_workspace": True}}
    )

    # Spawn agent in background — research=True enables web search + file writes
    asyncio.create_task(
        _stream_agent_to_card(
            card_id=card_id,
            prompt=research_prompt,
            directory=directory,
            agent_type=agent_type,
            space_id=card["space_id"],
            research=True,
        ),
        name=f"research_{card_id}",
    )

    log.info("start_research_initiated", card_id=card_id, has_user_prompt=bool(body.prompt))
    return {"status": "agent_running", "card_id": card_id}


# ---------- Context Group Management ----------


class MergeCardsRequest(BaseModel):
    card_ids: list[str]


@router.get("/cards/groups/{context_id}")
async def get_context_group(context_id: str):
    """Get context group metadata and member entity_ids."""
    db = await get_db()
    group_row = await db.execute_fetchall(
        "SELECT * FROM context_groups WHERE context_id = ?", (context_id,)
    )
    if not group_row:
        raise HTTPException(status_code=404, detail="Context group not found")

    members = await db.execute_fetchall(
        "SELECT entity_id, confidence, link_method, added_at FROM context_group_members WHERE context_id = ?",
        (context_id,),
    )
    cards = await db.execute_fetchall(
        "SELECT card_id, header, entity_id, status FROM action_cards WHERE context_id = ?",
        (context_id,),
    )

    return {
        "context_id": context_id,
        "label": group_row[0]["label"],
        "user_confirmed": bool(group_row[0]["user_confirmed"]),
        "user_split": bool(group_row[0]["user_split"]),
        "created_at": group_row[0]["created_at"],
        "members": [dict(m) for m in members],
        "cards": [{"card_id": c["card_id"], "header": c["header"], "entity_id": c["entity_id"], "status": c["status"]} for c in cards],
    }


@router.post("/cards/groups/{context_id}/unlink")
async def unlink_context_group(context_id: str):
    """Split a context group — cards revert to entity_id grouping.

    Sets user_split=TRUE so the system won't re-merge these cards.
    """
    db = await get_db()
    group_row = await db.execute_fetchall(
        "SELECT context_id FROM context_groups WHERE context_id = ?", (context_id,)
    )
    if not group_row:
        raise HTTPException(status_code=404, detail="Context group not found")

    # Fetch cards in this group before unlinking (for correction recording)
    group_cards = await db.execute_fetchall(
        """SELECT c.card_id, c.header, c.summary, c.space_id, e.source_platform
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           WHERE c.context_id = ?""",
        (context_id,),
    )

    # Mark as user-split so resolve_context_group won't re-merge
    await db.execute(
        "UPDATE context_groups SET user_split = TRUE WHERE context_id = ?",
        (context_id,),
    )
    # Remove context_id from all member cards
    await db.execute(
        "UPDATE action_cards SET context_id = NULL WHERE context_id = ?",
        (context_id,),
    )

    # Record unlink corrections for learning (pairwise between all cards)
    if len(group_cards) >= 2:
        space_id = group_cards[0]["space_id"]
        for i in range(len(group_cards)):
            for j in range(i + 1, len(group_cards)):
                a, b = group_cards[i], group_cards[j]
                try:
                    await db.execute(
                        """INSERT INTO context_corrections
                           (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                            platform_a, platform_b, action, space_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unlink', ?)""",
                        (a["card_id"], b["card_id"], a["header"], b["header"],
                         a["summary"], b["summary"],
                         a["source_platform"], b["source_platform"], space_id),
                    )
                except Exception as e:
                    log.debug("context_correction_insert_failed", error=str(e))

    await db.commit()

    log.info("context_group_unlinked", context_id=context_id)
    await manager.broadcast({"type": "context_group_unlinked", "payload": {"context_id": context_id}})
    return {"status": "unlinked", "context_id": context_id}


@router.post("/cards/groups/merge")
async def merge_cards(body: MergeCardsRequest):
    """Manually merge cards into a context group.

    Creates a user-confirmed context group for the specified cards.
    """
    import uuid

    if len(body.card_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 card_ids required")

    db = await get_db()

    # Fetch all specified cards (include summary + platform for correction recording)
    placeholders = ",".join("?" * len(body.card_ids))
    cards = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id, c.context_id, c.header, c.summary, c.space_id,
                   e.source_platform
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            WHERE c.card_id IN ({placeholders})""",
        body.card_ids,
    )
    if len(cards) < 2:
        raise HTTPException(status_code=404, detail="Not enough valid cards found")

    # Check if any card already belongs to a context group — extend it
    existing_context_id = None
    for c in cards:
        if c["context_id"]:
            existing_context_id = c["context_id"]
            break

    if existing_context_id:
        context_id = existing_context_id
        # Update the group to user-confirmed and clear any user_split
        await db.execute(
            "UPDATE context_groups SET user_confirmed = TRUE, user_split = FALSE WHERE context_id = ?",
            (context_id,),
        )
    else:
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"
        # Use the first card's header as the label
        label = cards[0]["header"]
        if len(label) > 60:
            label = label[:57] + "..."
        await db.execute(
            "INSERT INTO context_groups (context_id, label, user_confirmed) VALUES (?, ?, TRUE)",
            (context_id, label),
        )

    # Assign context_id to all cards and register entity memberships
    for c in cards:
        await db.execute(
            "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
            (context_id, c["card_id"]),
        )
        if c["entity_id"]:
            await db.execute(
                "INSERT OR IGNORE INTO context_group_members (context_id, entity_id, confidence, link_method) VALUES (?, ?, 1.0, 'user')",
                (context_id, c["entity_id"]),
            )
    await db.commit()

    # Record link corrections for learning (pairwise between cards from different entity groups)
    space_id = cards[0]["space_id"] if cards else None
    seen_pairs: set[tuple[str, str]] = set()
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            a, b = cards[i], cards[j]
            # Only record pairs from different entity groups (same-entity links are redundant)
            if a["entity_id"] and b["entity_id"] and a["entity_id"] == b["entity_id"]:
                continue
            pair_key = tuple(sorted([a["card_id"], b["card_id"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            try:
                await db.execute(
                    """INSERT INTO context_corrections
                       (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                        platform_a, platform_b, action, space_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'link', ?)""",
                    (a["card_id"], b["card_id"], a["header"], b["header"],
                     a["summary"], b["summary"],
                     a["source_platform"], b["source_platform"], space_id),
                )
            except Exception as e:
                log.debug("context_correction_insert_failed", error=str(e))
    await db.commit()

    log.info("context_group_merged", context_id=context_id, card_count=len(cards))
    await manager.broadcast({"type": "context_group_merged", "payload": {"context_id": context_id}})
    return {"status": "merged", "context_id": context_id, "card_count": len(cards)}
