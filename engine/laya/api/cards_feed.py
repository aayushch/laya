# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Feed read endpoints — flat list + grouped/paginated feed (split from cards_api — P7-6)."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import structlog
from fastapi import APIRouter

from laya.api.cards_common import CARD_SELECT_COLUMNS, _row_to_card
from laya.db.sqlite import get_db
from laya.models.card import (
    CardGroup, CardsListResponse, GroupedCardsResponse, GroupSummaryResponse,
    TagAssignment,
)

log = structlog.get_logger()
router = APIRouter()


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
        f"""SELECT {CARD_SELECT_COLUMNS}
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
    limit: int = 200,
    offset: int = 0,
) -> GroupedCardsResponse:
    """Return cards grouped by entity_id, filtered by date and space.

    Groups are capped at ``limit`` (default 200) after sorting, with ``offset``
    for "load more" pagination — without it the unbounded modes (all-days /
    bookmarked / related, where the date filter is dropped) returned every group
    at once, a multi-MB response that blocked the shared DB connection (review
    §2/§4 — P4-9). ``total_groups`` is always the full count and ``has_more``
    signals another page.
    """
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
    # Only joined when a search is active (aggregates each card's tag names once).
    search_join = ""
    if search:
        terms = [t for t in search.lower().split() if t]
        # De-correlate the tag match: aggregate each card's tag names ONCE via a
        # join to a grouped subquery (ctag.tag_names), instead of the old
        # correlated GROUP_CONCAT subquery that re-ran per row per term. Same
        # substring semantics; the per-row-per-term full scan is gone (review §2/§4
        # — P4-10). Also dropped the giant staged_output/suggested_actions JSON
        # blobs from the scanned fields — they dominated the LIKE cost and search
        # over raw action JSON is low value.
        search_join = (
            " LEFT JOIN (SELECT ta.target_id, GROUP_CONCAT(t.name) AS tag_names "
            "FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id "
            "WHERE ta.target_type = 'card' GROUP BY ta.target_id) ctag "
            "ON ctag.target_id = c.card_id"
        )
        search_fields = [
            "c.header", "c.summary", "c.category",
            "c.entity_id", "c.source_ref",
            "e.actor_name", "e.actor_email",
            "s.name",
            "c.persona", "c.priority", "c.status",
            "c.intelligence",
            "e.subject_title", "e.source_platform",
            "CASE c.privacy_tier WHEN 3 THEN 'confidential' WHEN 2 THEN 'internal' WHEN 1 THEN 'public' ELSE '' END",
            "ctag.tag_names",
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
        f"""SELECT {CARD_SELECT_COLUMNS}
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            LEFT JOIN spaces s ON c.space_id = s.space_id
            {search_join}
            {where_clause}
            ORDER BY c.created_at DESC""",
        params,
    )

    # Pass 1: Group by entity_id (structural grouping)
    groups: dict[str, list] = {}
    for row in rows:
        group_key = row["entity_id"] or f"singleton:{row['card_id']}"
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(row)

    # Pass 2: Merge entity groups that share a context_id (semantic grouping)
    from laya.config import load_settings
    settings = load_settings()
    smart_display = settings.get("smart_grouping", {}).get("smart_display", True)

    # Maps context_id -> label, tracks user_split exclusions
    context_labels: dict[str, str] = {}
    split_context_ids: set[str] = set()

    if smart_display:
        # Collect all distinct context_ids across cards
        all_context_ids: set[str] = set()
        for entity_rows in groups.values():
            for r in entity_rows:
                cid = r["context_id"]
                if cid:
                    all_context_ids.add(cid)

        if all_context_ids:
            placeholders = ",".join("?" * len(all_context_ids))
            ctx_rows = await db.execute_fetchall(
                f"SELECT context_id, label, user_split FROM context_groups WHERE context_id IN ({placeholders})",
                list(all_context_ids),
            )
            for cr in ctx_rows:
                if cr["user_split"]:
                    split_context_ids.add(cr["context_id"])
                else:
                    if cr["label"]:
                        context_labels[cr["context_id"]] = cr["label"]

            # Build context_id -> list of entity group keys
            ctx_to_groups: dict[str, list[str]] = {}
            for group_key, entity_rows in groups.items():
                # Use the most common context_id across cards in this entity group
                cid_counts: dict[str, int] = {}
                for r in entity_rows:
                    cid = r["context_id"]
                    if cid and cid not in split_context_ids:
                        cid_counts[cid] = cid_counts.get(cid, 0) + 1
                if cid_counts:
                    dominant_cid = max(cid_counts, key=cid_counts.get)  # type: ignore[arg-type]
                    ctx_to_groups.setdefault(dominant_cid, []).append(group_key)

            # Merge entity groups that share a context_id (only when 2+ entity groups)
            _MAX_ENTITY_GROUPS_PER_CONTEXT = 6
            merged_keys: set[str] = set()
            for cid, group_keys in ctx_to_groups.items():
                if len(group_keys) < 2:
                    continue
                # Skip context groups with only 1 distinct entity_id
                distinct_entities = {gk for gk in group_keys if not gk.startswith("singleton:")}
                if len(distinct_entities) < 2 and len(group_keys) - len(distinct_entities) == 0:
                    continue
                # Size cap: don't create mega-groups
                if len(group_keys) > _MAX_ENTITY_GROUPS_PER_CONTEXT:
                    continue

                # Merge all rows under the context_id key
                merged_rows: list = []
                for gk in group_keys:
                    merged_rows.extend(groups[gk])
                    merged_keys.add(gk)
                # entity_id ASC (same-entity cards together), created_at DESC within each
                merged_rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
                merged_rows.sort(key=lambda r: r["entity_id"] or "")
                groups[cid] = merged_rows

            # Remove the original entity groups that were merged
            for mk in merged_keys:
                if mk in groups:
                    del groups[mk]

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

    # Batch-fetch group summaries for all entity_ids and context group keys
    all_entity_ids = set()
    for group_key, entity_rows in groups.items():
        if group_key.startswith("ctx_"):
            all_entity_ids.add(group_key)
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
        is_context_group = group_key.startswith("ctx_")

        # slim=True: the list payload drops staged_output/suggested_actions; the
        # detail panel re-fetches the full card via GET /cards/{id} (P4-9).
        cards = [_row_to_card(r, slim=True) for r in entity_rows]
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

        if is_context_group:
            entity_id_val = group_key
            entity_title = context_labels.get(group_key) or meta.get("subject_title") or group_key
            # Collect distinct platforms from entity_ids
            seen_platforms: set[str] = set()
            ordered_platforms: list[str] = []
            for r in entity_rows:
                eid = r["entity_id"] or ""
                plat = eid.split(":")[0] if ":" in eid else ""
                if plat and plat not in seen_platforms:
                    seen_platforms.add(plat)
                    ordered_platforms.append(plat)
            # Primary platform from the most recent card
            top_eid = entity_rows[0]["entity_id"] or ""
            group_platform = top_eid.split(":")[0] if ":" in top_eid else meta.get("source_platform", "")
        else:
            entity_id_val = entity_rows[0]["entity_id"] or group_key
            entity_title = meta.get("subject_title") or entity_id_val
            group_platform = meta.get("source_platform", "")
            ordered_platforms = []

        group_summary = summary_map.get(entity_id_val) if len(cards) >= 2 else None

        # Entity-level tags (aggregate across all entity_ids in context groups)
        entity_tags_list: list[dict] = []
        if is_context_group:
            seen_tag_ids: set[str] = set()
            for r in entity_rows:
                eid = r["entity_id"]
                if eid:
                    for t in tags_map.get(("entity", eid), []):
                        if t["tag_id"] not in seen_tag_ids:
                            seen_tag_ids.add(t["tag_id"])
                            entity_tags_list.append(t)
        else:
            entity_tags_list = tags_map.get(("entity", entity_id_val), [])

        result.append(
            CardGroup(
                entity_id=entity_id_val,
                entity_title=entity_title,
                entity_url=meta.get("subject_url") if not is_context_group else None,
                platform=group_platform,
                card_count=len(cards),
                top_priority=top_priority,
                latest_at=latest_at,
                has_pending=has_pending,
                unread_count=unread_count,
                cards=cards,
                context_id=group_key if is_context_group else None,
                context_label=context_labels.get(group_key) if is_context_group else None,
                platforms=ordered_platforms if is_context_group and len(ordered_platforms) > 1 else None,
                group_summary=group_summary,
                tags=[TagAssignment(**t) for t in entity_tags_list],
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
        # Resolve the adjacent local dates that have cards. The previous approach
        # applied TODAY's fixed UTC offset to DATE(group_active_at) for every row —
        # wrong across a DST boundary (a historical date has a different offset) —
        # and ran two full-table DATE()-per-row scans. Instead compute the target
        # local day's UTC window (ZoneInfo has the IANA db, so DST is handled per
        # date), find the single boundary card with an indexed range lookup, and
        # convert just that one timestamp to a local date in Python (review §2/§4 — P4-16).
        local_tz = timezone.utc
        if tz:
            try:
                local_tz = ZoneInfo(tz)
            except (KeyError, ValueError):
                local_tz = timezone.utc

        def _utc_str_to_local_date(ts: str) -> str | None:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(ts[:19], fmt).replace(tzinfo=timezone.utc)
                    return dt.astimezone(local_tz).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    continue
            return None

        try:
            day_start_local = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=local_tz)
            next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            day_end_local = datetime.strptime(next_day, "%Y-%m-%d").replace(tzinfo=local_tz)
            day_start_utc = day_start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            day_end_utc = day_end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            day_start_utc = day_end_utc = None

        if day_start_utc is not None:
            prev_rows = await db.execute_fetchall(
                "SELECT group_active_at FROM action_cards "
                "WHERE group_active_at < ? AND group_active_at IS NOT NULL "
                "ORDER BY group_active_at DESC LIMIT 1",
                (day_start_utc,),
            )
            if prev_rows and prev_rows[0]["group_active_at"]:
                prev_date_val = _utc_str_to_local_date(prev_rows[0]["group_active_at"])
            next_rows = await db.execute_fetchall(
                "SELECT group_active_at FROM action_cards "
                "WHERE group_active_at >= ? "
                "ORDER BY group_active_at ASC LIMIT 1",
                (day_end_utc,),
            )
            if next_rows and next_rows[0]["group_active_at"]:
                next_date_val = _utc_str_to_local_date(next_rows[0]["group_active_at"])

    # Cap groups server-side after sorting; total_groups stays the full count so
    # the UI can page with "load more" (P4-9).
    total = len(result)
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    page = result[safe_offset : safe_offset + safe_limit]
    has_more = safe_offset + len(page) < total

    return GroupedCardsResponse(
        groups=page,
        total_groups=total,
        has_more=has_more,
        date=date,
        prev_date=prev_date_val,
        next_date=next_date_val,
        space_id=space_id,
    )
