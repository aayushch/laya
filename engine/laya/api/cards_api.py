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
from laya.models.card import CardGroup, CardResponse, CardsListResponse, GroupedCardsResponse, GroupSummaryResponse, StagedOutput, SuggestedAction, TagAssignment
from laya.pipeline.summarize import trigger_summary_status_update
from laya.tasks import create_task as create_tracked_task

log = structlog.get_logger()
router = APIRouter()


def _safe_privacy_tier(val) -> int:
    try:
        return max(1, min(3, int(val)))
    except (TypeError, ValueError):
        return 2


def _row_to_card(row) -> CardResponse:
    """Convert a SQLite Row to a CardResponse, deserializing JSON columns."""
    intelligence = None
    if row["intelligence"]:
        try:
            parsed = json.loads(row["intelligence"])
            intelligence = parsed if isinstance(parsed, list) else None
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

    source_context = None
    if "content_metadata" in row.keys() and row["content_metadata"]:
        try:
            meta = json.loads(row["content_metadata"])
            if meta.get("slack_channel_name"):
                source_context = "#" + meta["slack_channel_name"]
        except (json.JSONDecodeError, TypeError):
            pass

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
        privacy_tier=_safe_privacy_tier(row["privacy_tier"]),
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
        read_at=row["read_at"] if "read_at" in row.keys() else None,
        group_active_at=row["group_active_at"] if "group_active_at" in row.keys() else None,
        context_id=row["context_id"] if "context_id" in row.keys() else None,
        last_error=row["last_error"] if "last_error" in row.keys() else None,
        source_context=source_context,
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
                   c.space_id, c.bookmarked_at, c.read_at, c.group_active_at,
                   e.actor_name, e.actor_email, e.content_metadata,
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
    has_workspace: bool = False,
    unread_only: bool = False,
    related_entity_ids: str | None = None,
    search: str | None = None,
    tags: str | None = None,
) -> GroupedCardsResponse:
    """Return cards grouped by entity_id, filtered by date and space."""
    db = await get_db()

    conditions: list[str] = []
    params: list[Any] = []
    if related_entity_ids:
        eids = [e.strip() for e in related_entity_ids.split(",") if e.strip()]
        if eids:
            placeholders = ",".join("?" for _ in eids)
            conditions.append(f"c.entity_id IN ({placeholders})")
            params.extend(eids)
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
    if date and not bookmarked and not related_entity_ids:
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
    if has_workspace:
        conditions.append("c.has_workspace = 1")
    if unread_only:
        conditions.append("c.read_at IS NULL")
    if not show_archived:
        conditions.append("c.status != 'archived'")
    if search:
        terms = [t for t in search.lower().split() if t]
        search_fields = [
            "c.header", "c.summary", "c.category",
            "c.entity_id", "c.source_ref",
            "e.actor_name", "e.actor_email",
            "s.name",
            "c.persona", "c.priority", "c.status",
            "c.intelligence", "c.staged_output", "c.suggested_actions",
            "e.subject_title", "e.source_platform",
            "CASE c.privacy_tier WHEN 3 THEN 'confidential' WHEN 2 THEN 'internal' WHEN 1 THEN 'public' ELSE '' END",
            "(SELECT GROUP_CONCAT(t.name) FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND ta.target_id = c.card_id)",
        ]
        for term in terms:
            like_val = f"%{term}%"
            conditions.append(f"({' OR '.join(f'{f} LIKE ?' for f in search_fields)})")
            params.extend([like_val] * len(search_fields))
    if tags:
        tag_names = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_names:
            placeholders = ",".join("?" * len(tag_names))
            conditions.append(
                f"(c.card_id IN (SELECT ta.target_id FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND LOWER(t.name) IN ({placeholders}))"
                f" OR c.entity_id IN (SELECT ta.target_id FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'entity' AND LOWER(t.name) IN ({placeholders})))"
            )
            params.extend([n.lower() for n in tag_names] * 2)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                   c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                   c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                   c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                   c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                   c.space_id, c.bookmarked_at, c.read_at, c.group_active_at, c.context_id,
                   e.actor_name, e.actor_email, e.content_metadata,
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

    # Batch-fetch group summaries for all entity_ids present
    all_entity_ids = set()
    for entity_rows in groups.values():
        for r in entity_rows:
            eid = r["entity_id"]
            if eid:
                all_entity_ids.add(eid)
    summary_map: dict[str, GroupSummaryResponse] = {}
    if all_entity_ids:
        placeholders = ",".join("?" * len(all_entity_ids))
        summary_rows = await db.execute_fetchall(
            f"SELECT * FROM group_summaries WHERE entity_id IN ({placeholders})",
            list(all_entity_ids),
        )
        for sr in summary_rows:
            summary_map[sr["entity_id"]] = GroupSummaryResponse(
                entity_id=sr["entity_id"],
                headline=sr["headline"],
                summary=sr["summary"],
                key_events=json.loads(sr["key_events"] or "null"),
                current_status=sr["current_status"],
                pending_actions=json.loads(sr["pending_actions"] or "null"),
                card_count=sr["card_count"],
                card_ids=json.loads(sr["card_ids"] or "[]"),
                updated_at=sr["updated_at"],
            )

    # Batch-fetch tags for all cards and entity groups
    from laya.pipeline.tags import batch_load_tags
    all_card_ids = [r["card_id"] for rows_list in groups.values() for r in rows_list]
    tags_map = await batch_load_tags(all_card_ids, list(all_entity_ids) if all_entity_ids else None)

    result: list[CardGroup] = []
    for group_key, entity_rows in groups.items():
        cards = [_row_to_card(r) for r in entity_rows]
        # Inject tags into each card
        for card in cards:
            card_tags = tags_map.get(("card", card.card_id), [])
            card.tags = [TagAssignment(**t) for t in card_tags]

        meta = event_meta.get(entity_rows[0]["event_id"], {})
        top_priority = min(
            (c.priority for c in cards),
            key=lambda p: _PRIORITY_ORDER.get(p, 99),
        )
        latest_at = max((c.created_at or "") for c in cards)
        has_pending = any(c.status in ("pending", "ready") for c in cards)
        unread_count = sum(1 for c in cards if c.read_at is None)

        entity_id_val = entity_rows[0]["entity_id"] or group_key
        entity_title = meta.get("subject_title") or entity_id_val
        group_platform = meta.get("source_platform", "")
        group_summary = summary_map.get(entity_id_val) if len(cards) >= 2 else None

        # Entity-level tags
        entity_tags = tags_map.get(("entity", entity_id_val), [])

        result.append(
            CardGroup(
                entity_id=entity_id_val,
                entity_title=entity_title,
                entity_url=meta.get("subject_url"),
                platform=group_platform,
                card_count=len(cards),
                top_priority=top_priority,
                latest_at=latest_at,
                has_pending=has_pending,
                unread_count=unread_count,
                cards=cards,
                group_summary=group_summary,
                tags=[TagAssignment(**t) for t in entity_tags],
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
            "awaiting_input": 0, "failed": 1,
            "agent_running": 2, "pending": 3, "ready": 4,
            "done": 5, "dismissed": 6, "archived": 7,
        }
        _STATUS_LABEL = {
            "awaiting_input": "Input Needed", "failed": "Failed",
            "agent_running": "Agent Running",
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

    from laya.models.card_lifecycle import transition_card_status
    dismissed = 0
    for row in rows:
        try:
            await transition_card_status(row["card_id"], "dismissed", actor="user")
            dismissed += 1
        except ValueError:
            continue

    # Update summary for each dismissed card so items get strikethrough
    header_rows = await db.execute_fetchall(
        "SELECT card_id, header FROM action_cards WHERE entity_id = ? AND status = 'dismissed'",
        (entity_id,),
    )
    for hr in header_rows:
        create_tracked_task(
            trigger_summary_status_update(hr["card_id"], hr["header"], "dismissed"),
            name=f"summary_status_{hr['card_id']}",
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE entity_id = ? AND read_at IS NULL",
        (now, entity_id),
    )
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

    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(card_id, "archived", actor="user")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

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
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "archived"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "archived", "card_id": card_id}


@router.post("/cards/{card_id}/reopen")
async def reopen_card(card_id: str) -> dict:
    """Reopen a card, retrying the last failed stage when applicable.

    Retry strategy based on failed_stage:
    - agent_spawn / agent_execution: reset to ready so user can re-invoke agent
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
        # Agent failed — put card back to ready so user can re-invoke agent
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
        create_tracked_task(
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
                  c.space_id, c.bookmarked_at, c.read_at, c.group_active_at,
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

    card = _row_to_card(rows[0])
    # Hydrate tags
    tag_rows = await db.execute_fetchall(
        """SELECT t.tag_id, t.name AS tag_name, t.color, t.is_system, ta.assigned_by
           FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id
           WHERE ta.target_type = 'card' AND ta.target_id = ?
           ORDER BY t.name""",
        (card_id,),
    )
    card.tags = [TagAssignment(**dict(r)) for r in tag_rows]
    return card


@router.post("/cards/{card_id}/done")
async def mark_card_done(card_id: str) -> dict:
    """Mark an action card as done (user has reviewed/acted on it)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    doneable = {"pending", "ready", "awaiting_input"}
    if rows[0]["status"] not in doneable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' cannot be marked done"
        )

    current = rows[0]["status"]
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE action_cards
           SET status = 'done', previous_status = ?, resolved_at = ?, updated_at = ?,
               read_at = COALESCE(read_at, ?)
           WHERE card_id = ?""",
        (current, now, now, now, card_id),
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
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "done"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "done", "card_id": card_id}



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

    # Clean up research directory if it exists (best-effort)
    from laya.config import LAYA_HOME
    research_dir = LAYA_HOME / "tmp" / "research" / card_id
    if research_dir.exists():
        try:
            import shutil
            shutil.rmtree(research_dir)
        except Exception as e:
            log.warning("research_dir_cleanup_failed", card_id=card_id, error=str(e))

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

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET read_at = ? WHERE card_id = ? AND read_at IS NULL",
        (now, card_id),
    )
    await db.commit()
    return {"status": "read", "card_id": card_id, "read_at": now}


@router.post("/cards/group/{entity_id:path}/read-all")
async def mark_group_read(entity_id: str) -> dict:
    """Mark all cards in an entity group as read."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
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
    now = datetime.now(timezone.utc).isoformat()
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

    current = rows[0]["status"]
    reason = body.reason if body else None
    feedback_type = body.feedback_type if body else None

    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(
            card_id, "dismissed", actor="user",
            reason=reason, feedback_type=feedback_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

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
        create_tracked_task(
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
    updated_action: dict | None = None
    for action in actions:
        if action.get("action_id") == body.action_id:
            action["payload"].update(body.payload)
            # Mark that the user has manually edited this draft — this unlocks
            # the Polish action in the UI. Only set when caller did not supply
            # its own value (e.g. the polish task itself preserves the flag).
            if "_edited" not in body.payload:
                action["payload"]["_edited"] = True
            found = True
            updated_action = action
            break

    if not found:
        raise HTTPException(status_code=404, detail="Action not found")

    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    # Broadcast so other clients (and the feed's selectedCard snapshot) refresh
    # the action payload without needing a full card reload.
    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": body.action_id,
            "payload": {"payload": updated_action["payload"] if updated_action else {}},
        }
    )

    return {"status": "updated", "card_id": card_id, "action_id": body.action_id}


_POLISH_EDITABLE_FIELDS = ("body", "comment", "message", "description")


def _strip_fence_wrap(text: str) -> str:
    """Remove wrapping triple-backtick fences the LLM might add despite instructions."""
    if text.startswith("```") and text.endswith("```") and len(text) > 6:
        inner = text[3:-3]
        # Drop an optional language tag on the first line
        newline = inner.find("\n")
        if 0 <= newline <= 20 and inner[:newline].strip().isalnum():
            inner = inner[newline + 1:]
        return inner.strip()
    return text


def _find_editable_field(payload: dict) -> str | None:
    """Pick the main long-form text field in an action payload."""
    for key in _POLISH_EDITABLE_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return key
    return None


class PolishActionPayloadRequest(BaseModel):
    action_id: str


@router.post("/cards/{card_id}/action-payload/polish")
async def polish_action_payload(card_id: str, body: PolishActionPayloadRequest) -> dict:
    """Kick off an async LLM rewrite of a draft action payload.

    Returns immediately after marking the action as polishing. The actual LLM
    call runs as a background task; on completion the polished text is written
    back to the action payload and an `action_payload_updated` WS event fires.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT suggested_actions, space_id FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    actions = json.loads(row["suggested_actions"]) if row["suggested_actions"] else []
    target_action: dict | None = None
    for action in actions:
        if action.get("action_id") == body.action_id:
            target_action = action
            break
    if target_action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    payload = target_action.get("payload") or {}
    if payload.get("_polishing"):
        raise HTTPException(status_code=409, detail="Polish already in progress")

    editable_field = _find_editable_field(payload)
    if not editable_field:
        raise HTTPException(status_code=400, detail="No editable text field in this action")

    draft_text = payload[editable_field]
    if not isinstance(draft_text, str) or not draft_text.strip():
        raise HTTPException(status_code=400, detail="Draft is empty")

    platform = target_action.get("target_platform")
    space_id = row["space_id"] if "space_id" in row.keys() else None

    # Mark as polishing in DB so a re-mounted UI sees the spinner state.
    payload["_polishing"] = True
    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": body.action_id,
            "payload": {"payload": payload},
        }
    )

    create_tracked_task(
        _run_polish(
            card_id=card_id,
            action_id=body.action_id,
            editable_field=editable_field,
            draft_text=draft_text,
            platform=platform,
            space_id=space_id,
        ),
        name=f"polish_{card_id}_{body.action_id}",
    )

    return {"status": "polishing", "card_id": card_id, "action_id": body.action_id}


async def _run_polish(
    *,
    card_id: str,
    action_id: str,
    editable_field: str,
    draft_text: str,
    platform: str | None,
    space_id: str | None,
) -> None:
    """Background task: call the LLM, write polished text back to the action."""
    from laya.llm.client import llm_call
    from laya.llm.prompts.chat import build_polish_messages

    polish_schema = {
        "name": "polish_output",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "polished": {
                    "type": "string",
                    "description": "The polished/rewritten text.",
                },
            },
            "required": ["polished"],
            "additionalProperties": False,
        },
    }

    polished_text: str | None = None
    error_message: str | None = None
    try:
        response = await llm_call(
            role="chat",
            messages=build_polish_messages(draft_text, platform),
            step="polish_draft",
            temperature=0.4,
            max_tokens=2000,
            card_id=card_id,
            space_id=space_id,
            response_schema=polish_schema,
        )
        if response.parsed and isinstance(response.parsed, dict):
            polished_text = response.parsed.get("polished", "")
        else:
            polished_text = _strip_fence_wrap((response.content or "").strip())
        if not polished_text:
            error_message = "Polish returned empty response"
    except Exception as exc:  # noqa: BLE001 — surface any LLM failure to the user
        log.exception("polish_draft_failed", card_id=card_id, action_id=action_id)
        error_message = str(exc) or "Polish failed"

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT suggested_actions FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        return
    actions = json.loads(rows[0]["suggested_actions"]) if rows[0]["suggested_actions"] else []
    updated_payload: dict | None = None
    for action in actions:
        if action.get("action_id") != action_id:
            continue
        payload = action.get("payload") or {}
        payload["_polishing"] = False
        if polished_text and not error_message:
            payload[editable_field] = polished_text
            payload["_polished_at"] = datetime.now(timezone.utc).isoformat()
            payload.pop("_polish_error", None)
        elif error_message:
            payload["_polish_error"] = error_message
        action["payload"] = payload
        updated_payload = payload
        break

    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": action_id,
            "payload": {"payload": updated_payload or {}},
        }
    )


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
    valid_personas = {"ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"}

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
async def get_daily_summary(
    date: str | None = None,
    tz: str | None = None,
    space_id: str | None = None,
) -> dict:
    """Get daily summaries for a given date, grouped by space.

    When ``tz`` is provided the caller's local date is used for the DB
    lookup (summaries are stored under UTC dates).  Optionally filter to
    a single space via ``space_id``.
    """
    if not date:
        if tz:
            try:
                from zoneinfo import ZoneInfo
                local_now = datetime.now(ZoneInfo(tz))
                date = local_now.strftime("%Y-%m-%d")
            except (KeyError, ValueError):
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = await get_db()

    if space_id:
        rows = await db.execute_fetchall(
            """SELECT ds.space_id, ds.summary_json, ds.card_ids, ds.updated_at,
                      s.name AS space_name, s.color AS space_color
               FROM daily_summaries ds
               LEFT JOIN spaces s ON ds.space_id = s.space_id
               WHERE ds.date = ? AND ds.space_id = ?""",
            (date, space_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT ds.space_id, ds.summary_json, ds.card_ids, ds.updated_at,
                      s.name AS space_name, s.color AS space_color
               FROM daily_summaries ds
               LEFT JOIN spaces s ON ds.space_id = s.space_id
               WHERE ds.date = ?""",
            (date,),
        )

    space_summaries = []
    for row in rows:
        try:
            summary = json.loads(row["summary_json"])
            card_ids = json.loads(row["card_ids"])
        except json.JSONDecodeError:
            summary = None
            card_ids = []
        space_summaries.append({
            "space_id": row["space_id"] or "default",
            "space_name": row["space_name"] or "Default",
            "space_color": row["space_color"] or "#F97316",
            "summary": summary,
            "card_ids": card_ids,
            "updated_at": row["updated_at"],
        })

    return {
        "date": date,
        "space_summaries": space_summaries,
    }


class RunAgentRequest(BaseModel):
    prompt: str
    directory: str | None = None  # If omitted, defaults to ~/.laya/tmp/research/<card_id>/
    add_dirs: list[str] | None = None
    agent_type: str | None = None  # claude_code, gemini_cli, codex_cli
    mode: str | None = None  # e.g. plan, acceptEdits (claude), read-only, full-auto (codex)
    space_id: str | None = None
    files: list[str] | None = None  # Absolute paths to uploaded staging files



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

    Used by the run_agent() endpoint. Handles the
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

    # Update card status to agent_running and broadcast
    db_run = await get_db()
    await db_run.execute(
        "UPDATE action_cards SET status = 'agent_running', has_workspace = 1, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )
    await db_run.commit()
    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"has_workspace": True, "status": "agent_running", "session_id": session_id}}
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


class UploadAgentFilePathRequest(BaseModel):
    path: str


class DeleteStagingFileRequest(BaseModel):
    path: str


@router.post("/delete-agent-staging-file")
async def delete_agent_staging_file(body: DeleteStagingFileRequest) -> dict:
    """Delete a staged upload that the user removed before submitting.

    Path must be inside ~/.laya/tmp/agent-staging/; anything else is rejected
    to prevent path-traversal abuse. Missing files are treated as success
    (idempotent — safe to call twice).
    """
    from pathlib import Path as _Path

    from laya.config import LAYA_HOME

    staging_dir = (LAYA_HOME / "tmp" / "agent-staging").resolve()
    target = _Path(body.path).expanduser().resolve()

    try:
        target.relative_to(staging_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path is not inside the staging directory")

    if target.exists() and target.is_file():
        try:
            target.unlink()
            log.info("agent_staging_file_deleted", path=str(target))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"status": "ok"}


@router.post("/upload-agent-file-path")
async def upload_agent_file_path(body: UploadAgentFilePathRequest) -> dict:
    """Stage a reference file by local path.

    Tauri v2 on macOS (WKWebView) doesn't propagate OS-level file drops to the
    DOM, so the frontend uses Tauri's native drag-drop event which gives us
    absolute paths instead of File blobs. Since the engine runs locally, we can
    copy from that path into staging directly.
    """
    from pathlib import Path as _Path
    import mimetypes
    import shutil
    import uuid as _uuid

    from laya.config import LAYA_HOME

    src = _Path(body.path).expanduser()
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    staging_dir = LAYA_HOME / "tmp" / "agent-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    ext = "".join(c for c in src.suffix.lstrip(".").lower() if c.isalnum())[:8] or "bin"
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    dst = staging_dir / filename
    shutil.copy2(src, dst)

    content_type, _ = mimetypes.guess_type(str(src))
    log.info("agent_file_staged_by_path", src=str(src), dst=str(dst))
    return {
        "path": str(dst),
        "filename": src.name,  # preserve the user's original filename for the UI tile
        "size": dst.stat().st_size,
        "content_type": content_type or "application/octet-stream",
    }


@router.post("/upload-agent-file")
async def upload_agent_file(file: UploadFile = File(...)) -> dict:
    """Upload a reference file (image, PDF, text, etc.) for use with an agent run.

    Writes the POST body to ~/.laya/tmp/agent-staging/<uuid>.<ext> — a server-side
    staging area. On run-agent submit, the staged copy is moved into the card's
    attachments folder. The user's original file on disk is never touched.
    """
    from laya.config import LAYA_HOME

    staging_dir = LAYA_HOME / "tmp" / "agent-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Extension resolution: preserve original (sanitized) if present, else MIME-map.
    ext = ""
    if file.filename:
        parts = file.filename.rsplit(".", 1)
        if len(parts) == 2:
            candidate = "".join(c for c in parts[1].lower() if c.isalnum())[:8]
            if candidate:
                ext = candidate
    if not ext and file.content_type:
        ct_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/svg+xml": "svg",
            "application/pdf": "pdf",
            "text/plain": "txt",
            "text/csv": "csv",
            "application/json": "json",
            "text/markdown": "md",
        }
        ext = ct_map.get(file.content_type, "")
    if not ext:
        ext = "bin"

    import uuid as _uuid
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    filepath = staging_dir / filename

    content = await file.read()
    filepath.write_bytes(content)

    log.info("agent_file_uploaded", path=str(filepath), size=len(content), content_type=file.content_type)
    return {
        "path": str(filepath),
        "filename": filename,
        "size": len(content),
        "content_type": file.content_type or "application/octet-stream",
    }


@router.post("/cards/run-agent")
async def run_agent(body: RunAgentRequest) -> dict:
    """Create an ENGINEER card and spawn a coding agent directly.

    User-initiated agent run (triggered from the 'a' keyboard shortcut).
    Creates a card with source=laya, persona=ENGINEER, and immediately
    spawns the agent subprocess. The card then follows the normal workspace flow.
    """
    from pathlib import Path

    from laya.agents import session_manager
    from laya.config import LAYA_HOME
    from laya.models.workspace import AgentType

    # Resolve agent type
    if body.agent_type:
        try:
            agent_type = AgentType(body.agent_type)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown agent type: {body.agent_type}")
    else:
        agent_type = session_manager.get_configured_agent_type()

    # Generate event + card ids up front — we need card_id to provision the
    # per-card attachments folder before the agent spawns.
    import uuid
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    header = body.prompt[:120] + ("..." if len(body.prompt) > 120 else "")
    entity_id = f"laya:agent_run:{card_id}"

    # Resolve working directory. If the caller didn't specify one, use
    # ~/.laya/tmp/research/<card_id>/ and enable research mode (scoped writes
    # + web). The path substring '/tmp/research/' also lets workspace_api
    # auto-classify this session as research, which unlocks the file browser.
    card_dir = LAYA_HOME / "tmp" / "research" / card_id
    card_dir.mkdir(parents=True, exist_ok=True)
    if body.directory:
        working_dir = body.directory
        research_flag = False
    else:
        working_dir = str(card_dir)
        research_flag = True

    # Move staged uploads (server-side copies in ~/.laya/tmp/agent-staging/)
    # into card_dir/attachments/. The user's original files on disk are never
    # touched — only the copies the upload endpoint wrote.
    final_file_paths: list[str] = []
    if body.files:
        attachments_dir = card_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        for staged_path in body.files:
            src = Path(staged_path)
            if not src.exists():
                log.warning("agent_file_move_missing", path=staged_path, card_id=card_id)
                continue
            dst = attachments_dir / src.name
            try:
                src.rename(dst)
            except OSError as e:
                log.warning("agent_file_move_failed", src=str(src), dst=str(dst), error=str(e))
                continue
            final_file_paths.append(str(dst))

    # Build the effective prompt — append file references so the agent can
    # open them via its Read/file tool (agents don't have --attach flags).
    effective_prompt = body.prompt
    if final_file_paths:
        file_lines = "\n".join(f"- {p}" for p in final_file_paths)
        effective_prompt += (
            f"\n\nAttached reference files (use your Read/file tool to open them):\n{file_lines}"
        )

    db = await get_db()

    # Synthetic event so the FK constraint is satisfied.
    # Mark as 'completed' so the queue consumer never picks it up —
    # the raw_json is a flat dict (not a valid LayaEvent) and would
    # fail deserialization in _load_event().
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, content_body, raw_json,
            processed, processing_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            "completed",
        ),
    )

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            intelligence, staged_output, suggested_actions, status,
            privacy_tier, has_workspace, confidence, entity_id, source_ref,
            space_id, agent_prompt, group_active_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            event_id,
            "MEDIUM",
            "ENGINEER",
            "CODE",
            header,
            body.prompt,  # summary shows the original prompt (without attachment paths)
            json.dumps([]),
            json.dumps({}),
            json.dumps([]),
            "agent_running",
            2,
            True,
            1.0,
            entity_id,
            "Agent Run",
            body.space_id or "default",
            effective_prompt,  # agent_prompt includes attachment references
            now_ts,
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
                "privacy_tier": 2,
            },
        }
    )

    # Spawn agent in background. Attachments live inside card_dir/attachments/
    # and persist with the card — no post-run cleanup.
    create_tracked_task(
        _stream_agent_to_card(
            card_id=card_id,
            prompt=effective_prompt,
            directory=working_dir,
            agent_type=agent_type,
            space_id=body.space_id,
            add_dirs=body.add_dirs,
            mode=body.mode,
            research=research_flag,
        ),
        name=f"run_agent_{card_id}",
    )

    log.info("run_agent_initiated", card_id=card_id, agent_type=agent_type.value)
    return {"status": "agent_running", "card_id": card_id}


class RunEntityAgentRequest(BaseModel):
    prompt: str | None = None


@router.post("/entity/{entity_id:path}/run-agent")
async def run_entity_agent(entity_id: str, body: RunEntityAgentRequest) -> dict:
    """Start or resume an agent session for an entity group.

    Associates an agent at the entity level rather than per-card.
    Builds CONTEXT.md with group summary + card details, resolves repo,
    and spawns the agent. On subsequent calls, refreshes context and resumes.
    """
    from urllib.parse import unquote

    from laya.agents import session_manager
    from laya.agents.entity_context import (
        build_entity_agent_prompt,
        get_entity_research_dir,
        write_entity_context_file,
    )
    from laya.config import load_repos, load_settings
    from laya.workers.engineer import resolve_repo_path

    entity_id = unquote(entity_id)
    db = await get_db()

    # 1. Fetch all cards for this entity
    card_rows = await db.execute_fetchall(
        "SELECT card_id, status, space_id, entity_id FROM action_cards WHERE entity_id = ? ORDER BY created_at DESC",
        (entity_id,),
    )
    if not card_rows:
        raise HTTPException(status_code=404, detail="No cards found for this entity")

    # 2. Validate agent is configured
    settings = load_settings()
    agent_setting = settings.get("coding_agent", "none")
    if agent_setting == "none":
        raise HTTPException(
            status_code=409,
            detail="No coding agent configured. Set one in Settings > Agent.",
        )

    # 3. Check for existing running session
    existing = await session_manager.get_session_for_entity(entity_id)
    if existing and existing["status"] in ("starting", "running"):
        raise HTTPException(
            status_code=409,
            detail="An agent is already running for this entity",
        )

    space_id = card_rows[0]["space_id"] or "default"
    anchor_card_id = card_rows[0]["card_id"]

    # 4. Build CONTEXT.md
    await write_entity_context_file(entity_id, space_id)
    research_dir = get_entity_research_dir(entity_id)

    # 5. Resolve repo
    from laya.models.classification import Category, Persona, Priority, RouterOutput

    dummy_router = RouterOutput(
        persona=Persona.ENGINEER, priority=Priority.MEDIUM,
        category=Category.CODE, confidence=0.8, entities=[],
    )
    repo_path, other_repos = await resolve_repo_path(dummy_router, space_id=space_id)

    # 6. Determine cwd and add_dirs
    research_dir_str = str(research_dir)
    if repo_path:
        cwd = repo_path
        add_dirs = [research_dir_str] + [p for p in other_repos if p != research_dir_str]
    else:
        cwd = research_dir_str
        repos_data = load_repos()
        add_dirs = [r["path"] for r in repos_data.get("repos", []) if r.get("path")]

    # 7. Build agent prompt
    agent_prompt = build_entity_agent_prompt(
        entity_id=entity_id,
        research_dir=research_dir_str,
        repo_path=repo_path,
        user_prompt=body.prompt,
    )

    # 8. If a prior completed/paused session exists, resume it
    if existing and existing["status"] == "paused":
        # Refresh context and resume
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE action_cards SET has_workspace = 1, updated_at = ? WHERE entity_id = ?",
            (now, entity_id),
        )
        await db.execute(
            "UPDATE action_cards SET status = 'agent_running' WHERE card_id = ?",
            (anchor_card_id,),
        )
        await db.commit()

        for card_row in card_rows:
            payload: dict = {"has_workspace": True}
            if card_row["card_id"] == anchor_card_id:
                payload["status"] = "agent_running"
            await manager.broadcast(
                {"type": "card_updated", "card_id": card_row["card_id"], "payload": payload}
            )

        resume_text = body.prompt or "Continue working. Check CONTEXT.md for updated entity context."
        agent = await session_manager.resume_conversation(
            existing["session_id"], resume_text, add_dirs=add_dirs,
        )

        create_tracked_task(
            _stream_entity_agent(
                session_id=existing["session_id"],
                agent=agent,
                entity_id=entity_id,
                anchor_card_id=anchor_card_id,
            ),
            name=f"entity_agent_{entity_id}",
        )

        log.info("entity_agent_resumed", entity_id=entity_id, session_id=existing["session_id"])
        return {"status": "agent_running", "session_id": existing["session_id"], "card_id": anchor_card_id}

    # 9. New session — start in plan mode
    agent_type = session_manager.get_configured_agent_type()

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET has_workspace = 1, updated_at = ? WHERE entity_id = ?",
        (now, entity_id),
    )
    await db.execute(
        "UPDATE action_cards SET status = 'agent_running' WHERE card_id = ?",
        (anchor_card_id,),
    )
    await db.commit()

    for card_row in card_rows:
        payload: dict = {"has_workspace": True}
        if card_row["card_id"] == anchor_card_id:
            payload["status"] = "agent_running"
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_row["card_id"], "payload": payload}
        )

    session_id, agent = await session_manager.start_session(
        card_id=anchor_card_id,
        prompt=agent_prompt,
        repo_path=cwd,
        agent_type=agent_type,
        space_id=space_id,
        add_dirs=add_dirs,
        mode="plan",
        research=True,
        entity_id=entity_id,
    )

    create_tracked_task(
        _stream_entity_agent(
            session_id=session_id,
            agent=agent,
            entity_id=entity_id,
            anchor_card_id=anchor_card_id,
        ),
        name=f"entity_agent_{entity_id}",
    )

    log.info("entity_agent_started", entity_id=entity_id, session_id=session_id, anchor=anchor_card_id)
    return {"status": "agent_running", "session_id": session_id, "card_id": anchor_card_id}


async def _stream_entity_agent(
    session_id: str,
    agent: "Any",
    entity_id: str,
    anchor_card_id: str,
) -> None:
    """Background task: stream agent events for an entity-level session.

    Similar to _stream_agent_to_card but broadcasts updates keyed by the
    anchor card and updates has_workspace on all entity cards.
    """
    from laya.agents import session_manager
    from laya.models.workspace import SessionStatus

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
                    db2 = await get_db()
                    await db2.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (anchor_card_id,),
                    )
                    await db2.commit()
                    await manager.broadcast(
                        {"type": "card_updated", "card_id": anchor_card_id, "payload": {"status": "awaiting_input"}}
                    )
                await manager.broadcast(
                    {"type": "approval_request", "card_id": anchor_card_id, "session_id": session_id, "payload": ws_event.content}
                )
            elif ws_event.event_type.value == "error":
                findings["last_error"] = ws_event.content.get("error", "")
                await manager.broadcast(
                    {"type": "agent_error", "card_id": anchor_card_id, "session_id": session_id, "payload": ws_event.content}
                )

            if ws_event.event_type.value == "agent_message" and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")
            if ws_event.event_type.value == "status_change":
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error("entity_agent_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        db3 = await get_db()
        await db3.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (anchor_card_id,),
        )
        await db3.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": anchor_card_id, "payload": {"status": "failed"}}
        )
        return

    final_status = agent.get_status()
    db4 = await get_db()

    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

        agent_plan = findings.get("agent_plan", "")
        agent_result = findings.get("agent_result", "")
        staged_content = agent_plan or agent_result
        if staged_content:
            staged_type = "agent_plan" if agent_plan else "agent_result"
            await db4.execute(
                "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (json.dumps({"type": staged_type, "content": staged_content}), anchor_card_id),
            )

        has_unanswered = await session_manager.has_unanswered_questions(session_id)
        card_status = "awaiting_input" if has_unanswered else "ready"

        await db4.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_status, anchor_card_id),
        )
        await db4.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": anchor_card_id, "payload": {"status": card_status}}
        )
        await manager.broadcast(
            {"type": "agent_completed", "card_id": anchor_card_id, "session_id": session_id, "payload": {"findings": findings}}
        )
    elif final_status == SessionStatus.CANCELLED:
        await session_manager.complete_session(session_id, error="Cancelled by user")
        await db4.execute(
            "UPDATE action_cards SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (anchor_card_id,),
        )
        await db4.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": anchor_card_id, "payload": {"status": "ready"}}
        )
    else:
        last_error = findings.get("last_error", "")
        error_msg = f"Agent ended with status: {final_status.value}"
        if last_error:
            error_msg += f" -- {last_error}"
        log.error("entity_agent_failed", session_id=session_id, error=error_msg)
        await session_manager.complete_session(session_id, error=error_msg)
        await db4.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (anchor_card_id,),
        )
        await db4.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": anchor_card_id, "payload": {"status": "failed"}}
        )


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
        "SELECT card_id, confidence, link_method, added_at FROM context_group_members WHERE context_id = ?",
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


@router.get("/cards/{card_id}/related")
async def get_related_cards(card_id: str):
    """Return individual cards related to this card via context association."""
    db = await get_db()

    card_row = await db.execute_fetchall(
        "SELECT entity_id, context_id FROM action_cards WHERE card_id = ?", (card_id,),
    )
    if not card_row:
        raise HTTPException(status_code=404, detail="Card not found")

    # Find all non-split context groups containing this card
    ctx_rows = await db.execute_fetchall(
        """SELECT m.context_id, g.label
           FROM context_group_members m
           JOIN context_groups g ON g.context_id = m.context_id
           WHERE m.card_id = ? AND g.user_split = FALSE""",
        (card_id,),
    )
    if not ctx_rows:
        return {"card_id": card_id, "related_cards": [], "total_related_cards": 0}

    ctx_ids = list({r["context_id"] for r in ctx_rows})
    ctx_labels = {r["context_id"]: r["label"] for r in ctx_rows}

    # Get all OTHER card_ids in those context groups
    placeholders = ",".join("?" * len(ctx_ids))
    member_rows = await db.execute_fetchall(
        f"""SELECT m.card_id, m.context_id, m.confidence, m.link_method
            FROM context_group_members m
            WHERE m.context_id IN ({placeholders}) AND m.card_id != ?""",
        ctx_ids + [card_id],
    )
    if not member_rows:
        return {"card_id": card_id, "related_cards": [], "total_related_cards": 0}

    # Fetch card details
    related_card_ids = list({r["card_id"] for r in member_rows})
    cp = ",".join("?" * len(related_card_ids))
    card_rows = await db.execute_fetchall(
        f"SELECT card_id, header, entity_id, status FROM action_cards WHERE card_id IN ({cp})",
        related_card_ids,
    )

    member_info: dict[str, dict] = {}
    for r in member_rows:
        cid = r["card_id"]
        if cid not in member_info or r["confidence"] > member_info[cid].get("confidence", 0):
            member_info[cid] = {"context_id": r["context_id"], "confidence": r["confidence"], "link_method": r["link_method"]}

    related = []
    for c in card_rows:
        info = member_info.get(c["card_id"], {})
        related.append({
            "card_id": c["card_id"],
            "header": c["header"],
            "entity_id": c["entity_id"],
            "status": c["status"],
            "context_id": info.get("context_id", ""),
            "context_label": ctx_labels.get(info.get("context_id", ""), ""),
            "confidence": info.get("confidence", 0),
            "link_method": info.get("link_method", ""),
        })

    related.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "card_id": card_id,
        "related_cards": related,
        "total_related_cards": len(related),
    }


@router.post("/cards/{card_id}/unlink-related")
async def unlink_related_card(card_id: str):
    """Remove a single card from all its context groups."""
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT entity_id FROM action_cards WHERE card_id = ?", (card_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")

    # Find all context groups this card belongs to
    memberships = await db.execute_fetchall(
        "SELECT context_id FROM context_group_members WHERE card_id = ?", (card_id,),
    )
    affected_ctx_ids = [r["context_id"] for r in memberships]

    # Remove the card from all context groups
    await db.execute("DELETE FROM context_group_members WHERE card_id = ?", (card_id,))
    await db.execute("UPDATE action_cards SET context_id = NULL WHERE card_id = ?", (card_id,))

    # Dissolve context groups that have <=1 member remaining
    for ctx_id in affected_ctx_ids:
        remaining = await db.execute_fetchall(
            "SELECT COUNT(*) AS cnt FROM context_group_members WHERE context_id = ?", (ctx_id,),
        )
        if remaining[0]["cnt"] <= 1:
            await db.execute(
                "UPDATE context_groups SET user_split = TRUE WHERE context_id = ?", (ctx_id,),
            )

    await db.commit()
    log.info("card_unlinked_related", card_id=card_id, groups=affected_ctx_ids)
    await manager.broadcast({"type": "context_group_unlinked", "payload": {"card_id": card_id}})
    return {"status": "unlinked", "card_id": card_id}


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
    # Remove context_id from all member cards.
    await db.execute(
        "UPDATE action_cards SET context_id = NULL WHERE context_id = ?",
        (context_id,),
    )

    # Record unlink corrections for learning (anchor-based: first card paired with each other)
    if len(group_cards) >= 2:
        space_id = group_cards[0]["space_id"]
        anchor = group_cards[0]
        for other in group_cards[1:]:
            try:
                await db.execute(
                    """INSERT INTO context_corrections
                       (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                        platform_a, platform_b, action, space_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unlink', ?)""",
                    (anchor["card_id"], other["card_id"], anchor["header"], other["header"],
                     anchor["summary"], other["summary"],
                     anchor["source_platform"], other["source_platform"], space_id),
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

    # Assign context_id to all cards and register card-level memberships
    for c in cards:
        await db.execute(
            "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
            (context_id, c["card_id"]),
        )
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, 1.0, 'user')",
            (context_id, c["card_id"]),
        )
    await db.commit()

    # Record link corrections for learning (anchor-based: first card paired with each other)
    space_id = cards[0]["space_id"] if cards else None
    anchor = cards[0]
    for other in cards[1:]:
        # Only record pairs from different entity groups (same-entity links are redundant)
        if anchor["entity_id"] and other["entity_id"] and anchor["entity_id"] == other["entity_id"]:
            continue
        try:
            await db.execute(
                """INSERT INTO context_corrections
                   (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                    platform_a, platform_b, action, space_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'link', ?)""",
                (anchor["card_id"], other["card_id"], anchor["header"], other["header"],
                 anchor["summary"], other["summary"],
                 anchor["source_platform"], other["source_platform"], space_id),
            )
        except Exception as e:
            log.debug("context_correction_insert_failed", error=str(e))
    await db.commit()

    log.info("context_group_merged", context_id=context_id, card_count=len(cards))
    await manager.broadcast({"type": "context_group_merged", "payload": {"context_id": context_id}})
    return {"status": "merged", "context_id": context_id, "card_count": len(cards)}


# ---------------------------------------------------------------------------
# Group summary endpoints
# ---------------------------------------------------------------------------


@router.get("/cards/groups/{entity_id:path}/summary")
async def get_group_summary(entity_id: str):
    """Return the rolling summary for an entity group."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM group_summaries WHERE entity_id = ?",
        (entity_id,),
    )
    row = rows[0] if rows else None
    if not row:
        raise HTTPException(status_code=404, detail="No summary for this entity group")
    return GroupSummaryResponse(
        entity_id=row["entity_id"],
        headline=row["headline"],
        summary=row["summary"],
        key_events=json.loads(row["key_events"] or "null"),
        current_status=row["current_status"],
        pending_actions=json.loads(row["pending_actions"] or "null"),
        card_count=row["card_count"],
        card_ids=json.loads(row["card_ids"] or "[]"),
        updated_at=row["updated_at"],
    )


@router.post("/cards/groups/{entity_id:path}/summary/regenerate")
async def regenerate_summary(entity_id: str):
    """Force full regeneration of a group summary from all cards."""
    from laya.pipeline.group_summary import regenerate_group_summary

    result = await regenerate_group_summary(entity_id)
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Could not generate summary — entity group may have fewer than 2 cards or LLM call failed",
        )
    return result
