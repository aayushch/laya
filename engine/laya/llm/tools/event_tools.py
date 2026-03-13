"""Event-related tool implementations."""

from __future__ import annotations

import json
from typing import Any

from laya.db.sqlite import get_db


async def search_events(
    query: str | None = None,
    platform: str | None = None,
    actor: str | None = None,
    limit: int = 10,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Search events by keyword, platform, or actor."""
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []

    if query:
        keywords = [w for w in query.split() if len(w) >= 2]
        for kw in keywords[:5]:
            conditions.append("(subject_title LIKE ? OR content_body LIKE ?)")
            params.extend([f"%{kw}%"] * 2)

    if platform:
        conditions.append("source_platform = ?")
        params.append(platform)

    if actor:
        conditions.append("(actor_name LIKE ? OR actor_email LIKE ?)")
        params.extend([f"%{actor}%"] * 2)

    if space_id:
        conditions.append("space_id = ?")
        params.append(space_id)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(min(limit, 25))

    rows = await db.execute_fetchall(
        f"""SELECT event_id, timestamp, source_platform, actor_name, actor_email,
                   subject_title, subject_url, space_id
            FROM events WHERE {where}
            ORDER BY timestamp DESC LIMIT ?""",
        params,
    )

    return {
        "events": [
            {
                "event_id": r["event_id"],
                "timestamp": r["timestamp"],
                "platform": r["source_platform"],
                "actor_name": r["actor_name"],
                "actor_email": r["actor_email"],
                "subject_title": r["subject_title"],
                "subject_url": r["subject_url"],
                "space_id": r["space_id"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


async def get_event(event_id: str) -> dict[str, Any]:
    """Get full details of a specific event."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT event_id, timestamp, source_platform, source_raw_event_type,
                  actor_name, actor_email, actor_handle, actor_relationship,
                  subject_type, subject_id, subject_title, subject_url,
                  content_body, content_metadata, space_id, created_at
           FROM events WHERE event_id = ?""",
        (event_id,),
    )
    if not rows:
        return {"error": f"Event '{event_id}' not found"}

    r = rows[0]
    event: dict[str, Any] = {
        "event_id": r["event_id"],
        "timestamp": r["timestamp"],
        "platform": r["source_platform"],
        "event_type": r["source_raw_event_type"],
        "actor": {
            "name": r["actor_name"],
            "email": r["actor_email"],
            "handle": r["actor_handle"],
            "relationship": r["actor_relationship"],
        },
        "subject": {
            "type": r["subject_type"],
            "id": r["subject_id"],
            "title": r["subject_title"],
            "url": r["subject_url"],
        },
        "content_body": r["content_body"],
        "space_id": r["space_id"],
    }

    if r["content_metadata"]:
        try:
            event["content_metadata"] = json.loads(r["content_metadata"])
        except json.JSONDecodeError:
            pass

    return {"event": event}


async def get_recent_activity(
    hours: int = 24,
    limit: int = 10,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Get recent events and cards from the last N hours."""
    db = await get_db()
    space_filter = "AND space_id = ?" if space_id else ""
    space_params: list[Any] = [space_id] if space_id else []

    # Recent events
    event_rows = await db.execute_fetchall(
        f"""SELECT event_id, timestamp, source_platform, actor_name, subject_title
            FROM events
            WHERE timestamp >= datetime('now', '-{int(hours)} hours')
            {space_filter}
            ORDER BY timestamp DESC LIMIT ?""",
        [*space_params, min(limit, 25)],
    )

    # Recent cards
    card_rows = await db.execute_fetchall(
        f"""SELECT card_id, header, summary, status, priority, created_at
            FROM action_cards
            WHERE created_at >= datetime('now', '-{int(hours)} hours')
            {space_filter}
            ORDER BY created_at DESC LIMIT ?""",
        [*space_params, min(limit, 25)],
    )

    return {
        "hours": hours,
        "recent_events": [
            {
                "event_id": r["event_id"],
                "timestamp": r["timestamp"],
                "platform": r["source_platform"],
                "actor": r["actor_name"],
                "subject": r["subject_title"],
            }
            for r in event_rows
        ],
        "recent_cards": [
            {
                "card_id": r["card_id"],
                "header": r["header"],
                "summary": r["summary"],
                "status": r["status"],
                "priority": r["priority"],
                "created_at": r["created_at"],
            }
            for r in card_rows
        ],
        "event_count": len(event_rows),
        "card_count": len(card_rows),
    }
