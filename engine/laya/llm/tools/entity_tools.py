# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Entity-related tool implementations."""

from __future__ import annotations

import json
from typing import Any

from laya.db.sqlite import get_db
from laya.llm.tools.constants import ENTITY_SEARCH_DEFAULT, ENTITY_SEARCH_MAX


async def search_entities(
    query: str | None = None,
    entity_type: str | None = None,
    limit: int = ENTITY_SEARCH_DEFAULT,
    offset: int = 0,
) -> dict[str, Any]:
    """Search cross-platform entities."""
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []

    if query:
        keywords = [w for w in query.split() if len(w) >= 2]
        for kw in keywords[:5]:
            conditions.append("canonical_name LIKE ?")
            params.append(f"%{kw}%")

    if entity_type:
        conditions.append("entity_type = ?")
        params.append(entity_type)

    where = " AND ".join(conditions) if conditions else "1=1"

    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) as cnt FROM entities WHERE {where}",
        params,
    )
    total = count_rows[0]["cnt"] if count_rows else 0

    capped_limit = min(limit, ENTITY_SEARCH_MAX)
    rows = await db.execute_fetchall(
        f"""SELECT entity_id, entity_type, canonical_name, platform_refs, confidence
            FROM entities WHERE {where}
            ORDER BY confidence DESC LIMIT ? OFFSET ?""",
        [*params, capped_limit, offset],
    )

    return {
        "entities": [
            {
                "entity_id": r["entity_id"],
                "entity_type": r["entity_type"],
                "canonical_name": r["canonical_name"],
                "platform_refs": json.loads(r["platform_refs"]) if r["platform_refs"] else {},
                "confidence": r["confidence"],
            }
            for r in rows
        ],
        "count": len(rows),
        "total": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


async def get_entity(entity_id: str) -> dict[str, Any]:
    """Get full entity details including related cards and events."""
    db = await get_db()

    # Get entity
    rows = await db.execute_fetchall(
        """SELECT entity_id, entity_type, canonical_name, platform_refs,
                  confidence, link_method, created_at, updated_at
           FROM entities WHERE entity_id = ?""",
        (entity_id,),
    )
    if not rows:
        return {"error": f"Entity '{entity_id}' not found"}

    r = rows[0]
    platform_refs = json.loads(r["platform_refs"]) if r["platform_refs"] else {}

    entity: dict[str, Any] = {
        "entity_id": r["entity_id"],
        "entity_type": r["entity_type"],
        "canonical_name": r["canonical_name"],
        "platform_refs": platform_refs,
        "confidence": r["confidence"],
        "link_method": r["link_method"],
    }

    # Find related cards via the entity_id FK on action_cards
    related_cards = await db.execute_fetchall(
        """SELECT card_id, header, summary, status, priority, created_at
           FROM action_cards
           WHERE entity_id = ?
           ORDER BY created_at DESC LIMIT 20""",
        (entity_id,),
    )
    entity["related_cards"] = [
        {
            "card_id": c["card_id"],
            "header": c["header"],
            "summary": c["summary"],
            "status": c["status"],
            "priority": c["priority"],
            "created_at": c["created_at"],
        }
        for c in related_cards
    ]

    # Find related events via subject refs from platform_refs
    subject_ids = []
    for refs in platform_refs.values():
        if isinstance(refs, list):
            subject_ids.extend(refs)

    if subject_ids:
        placeholders = ",".join("?" * len(subject_ids[:10]))
        related_events = await db.execute_fetchall(
            f"""SELECT event_id, source_platform, subject_title, timestamp
                FROM events
                WHERE subject_id IN ({placeholders})
                ORDER BY timestamp DESC LIMIT 10""",
            subject_ids[:10],
        )
        entity["related_events"] = [
            {
                "event_id": e["event_id"],
                "platform": e["source_platform"],
                "subject": e["subject_title"],
                "timestamp": e["timestamp"],
            }
            for e in related_events
        ]
    else:
        entity["related_events"] = []

    return {"entity": entity}
