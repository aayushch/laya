# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared tag helpers — used by emit, processing rules, and tags API."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import structlog

from laya.db.sqlite import get_db
from laya.db.timeutil import db_ts
from laya.models.event import LayaEvent

log = structlog.get_logger()

TAG_SOFT_CAP = 10

# Limits for the tag names shown to the stager (see select_prompt_tags).
#
# When the tags table has at most this many names, all of them are shown.
PROMPT_TAGS_SHOW_ALL_MAX = 200
# How many of the most used tags of the last PROMPT_TAGS_RECENT_DAYS days are shown.
PROMPT_TAGS_RECENT_TOP = 150
PROMPT_TAGS_RECENT_DAYS = 30
# How many of the tags most often given to the same actor's events are shown.
PROMPT_TAGS_ACTOR_TOP = 30
# How many tags whose words all appear in the event's text are shown.
PROMPT_TAGS_TEXT_MATCH_MAX = 60

_WORD_RE = re.compile(r"[a-z0-9]+")


async def select_prompt_tags(db, event: LayaEvent) -> list[str]:
    """Pick the existing tag names to show the stager for one event.

    The stager is asked to reuse existing tag names. Showing it every name does
    not scale: each new tag would be added to every later prompt, and with a
    few thousand tags the list becomes most of the stager's input. This
    function returns a short list of the names the event is most likely to
    need. The stager can still suggest a name that is not in the list, and
    persist_suggested_tags reuses an existing tag when the name matches one.

    The list is built from five sources, in this order, without duplicates:

    1. Tags on earlier cards of the same entity, and tags assigned to the
       entity itself.
    2. Tags whose words all appear in the event's title or body. A tag named
       "integration-test" matches an event that mentions both "integration"
       and "test". At most PROMPT_TAGS_TEXT_MATCH_MAX are taken, names with
       more words first.
    3. The PROMPT_TAGS_ACTOR_TOP tags most often given to events of the same
       actor (matched by email, or by name when the event has no email).
    4. The PROMPT_TAGS_RECENT_TOP most used tags of the last
       PROMPT_TAGS_RECENT_DAYS days.
    5. System tags (for example "spam"), which are always shown.

    When the tags table has at most PROMPT_TAGS_SHOW_ALL_MAX names, all of
    them are returned in alphabetical order and the sources are not queried.
    """
    tag_rows = await db.execute_fetchall("SELECT name, is_system FROM tags ORDER BY name")
    all_names = [r["name"] for r in tag_rows]
    if len(all_names) <= PROMPT_TAGS_SHOW_ALL_MAX:
        return all_names

    selected: list[str] = []
    seen: set[str] = set()

    def add(names) -> None:
        for name in names:
            if name not in seen:
                seen.add(name)
                selected.append(name)

    # 1. Same entity. The event's own card is excluded: it exists already when
    # a card is reprocessed, and its old tags must not be fed back as a hint.
    entity_id = event.entity_id
    rows = await db.execute_fetchall(
        """SELECT DISTINCT t.name FROM tag_assignments ta
           JOIN tags t ON t.tag_id = ta.tag_id
           WHERE (ta.target_type = 'card' AND ta.target_id IN (
                      SELECT card_id FROM action_cards
                      WHERE entity_id = ? AND event_id != ?))
              OR (ta.target_type = 'entity' AND ta.target_id = ?)
           ORDER BY t.name""",
        (entity_id, event.event_id, entity_id),
    )
    add(r["name"] for r in rows)

    # 2. Words of the event text.
    event_words = set(_WORD_RE.findall(f"{event.subject.title} {event.content.body}".lower()))
    matches: list[tuple[int, int, str]] = []
    for name in all_names:
        tag_words = _WORD_RE.findall(name.lower())
        if tag_words and all(w in event_words for w in tag_words):
            matches.append((-len(tag_words), -len(name), name))
    matches.sort()
    add(name for _, _, name in matches[:PROMPT_TAGS_TEXT_MATCH_MAX])

    # 3. Same actor. The subquery picks the actor's cards first, so SQLite reads
    # only their tag assignments instead of scanning the whole assignments table.
    actor_column = "actor_email" if event.actor.email else "actor_name"
    actor_value = event.actor.email or event.actor.name
    if actor_value:
        rows = await db.execute_fetchall(
            f"""SELECT t.name, COUNT(*) AS uses FROM tag_assignments ta
                JOIN tags t ON t.tag_id = ta.tag_id
                WHERE ta.target_type = 'card' AND ta.target_id IN (
                    SELECT ac.card_id FROM events e
                    JOIN action_cards ac ON ac.event_id = e.event_id
                    WHERE e.{actor_column} = ? AND e.event_id != ?)
                GROUP BY ta.tag_id
                ORDER BY uses DESC, t.name
                LIMIT ?""",
            (actor_value, event.event_id, PROMPT_TAGS_ACTOR_TOP),
        )
        add(r["name"] for r in rows)

    # 4. Most used recently. tag_assignments has no index on created_at.
    # Assignment ids grow with time, so the subquery finds the newest
    # assignment older than the cutoff by walking the primary key backwards,
    # and the outer query then reads only the rows after it.
    cutoff = db_ts(datetime.now(timezone.utc) - timedelta(days=PROMPT_TAGS_RECENT_DAYS))
    rows = await db.execute_fetchall(
        """SELECT t.name, COUNT(*) AS uses FROM tag_assignments ta
           JOIN tags t ON t.tag_id = ta.tag_id
           WHERE ta.id > (SELECT COALESCE(MAX(id), 0) FROM tag_assignments
                          WHERE created_at < ?)
           GROUP BY ta.tag_id
           ORDER BY uses DESC, t.name
           LIMIT ?""",
        (cutoff, PROMPT_TAGS_RECENT_TOP),
    )
    add(r["name"] for r in rows)

    # 5. System tags.
    add(r["name"] for r in tag_rows if r["is_system"])

    return selected


async def persist_suggested_tags(card_id: str, tag_names: list[str]) -> list[str]:
    """Get-or-create tags and assign to a card. Returns the names actually assigned."""
    db = await get_db()
    assigned: list[str] = []

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM tag_assignments WHERE target_type = 'card' AND target_id = ?",
        (card_id,),
    )
    current_count = count_rows[0]["cnt"]

    for raw_name in tag_names[:3]:
        name = raw_name.strip().lower()
        if not name or len(name) > 50:
            continue
        if current_count >= TAG_SOFT_CAP:
            break
        rows = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
        if rows:
            tag_id = rows[0]["tag_id"]
        else:
            await db.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            await db.commit()
            new = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
            tag_id = new[0]["tag_id"]
        await db.execute(
            "INSERT OR IGNORE INTO tag_assignments (tag_id, target_type, target_id, assigned_by) VALUES (?, 'card', ?, 'stager')",
            (tag_id, card_id),
        )
        assigned.append(name)
        current_count += 1

    if assigned:
        await db.commit()
    return assigned


async def get_tags_csv(card_id: str) -> str:
    """Return comma-separated tag names for a card (for ChromaDB metadata)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT t.name FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND ta.target_id = ?",
        (card_id,),
    )
    return ",".join(r["name"] for r in rows) if rows else ""


async def update_card_tags_in_chromadb(card_id: str) -> None:
    """Re-upsert ChromaDB metadata with current tags for a card."""
    from laya.db.chromadb_store import get_collection

    tags_csv = await get_tags_csv(card_id)
    try:
        collection = get_collection()
        existing = collection.get(ids=[card_id], include=["metadatas"])
        if existing and existing["metadatas"]:
            metadata = existing["metadatas"][0]
            metadata["tags"] = tags_csv
            collection.update(ids=[card_id], metadatas=[metadata])
    except Exception as e:
        log.warning("chromadb_tag_update_failed", card_id=card_id, error=str(e))


async def get_card_tag_names(card_id: str) -> list[str]:
    """Return tag names for a card."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT t.name FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id WHERE ta.target_type = 'card' AND ta.target_id = ?",
        (card_id,),
    )
    return [r["name"] for r in rows]


async def batch_load_tags(
    card_ids: list[str], entity_ids: list[str] | None = None
) -> dict[tuple[str, str], list[dict]]:
    """Batch-load tags for cards and entity groups. Returns {(target_type, target_id): [tag dicts]}."""
    db = await get_db()
    result: dict[tuple[str, str], list[dict]] = {}
    if not card_ids and not entity_ids:
        return result

    conditions = []
    params: list[str] = []
    if card_ids:
        placeholders = ",".join("?" * len(card_ids))
        conditions.append(f"(ta.target_type = 'card' AND ta.target_id IN ({placeholders}))")
        params.extend(card_ids)
    if entity_ids:
        placeholders = ",".join("?" * len(entity_ids))
        conditions.append(f"(ta.target_type = 'entity' AND ta.target_id IN ({placeholders}))")
        params.extend(entity_ids)

    query = f"""
        SELECT ta.target_type, ta.target_id, t.tag_id, t.name, t.color, t.is_system, ta.assigned_by
        FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id
        WHERE {' OR '.join(conditions)}
        ORDER BY t.name
    """
    rows = await db.execute_fetchall(query, tuple(params))
    for r in rows:
        key = (r["target_type"], r["target_id"])
        if key not in result:
            result[key] = []
        result[key].append({
            "tag_id": r["tag_id"],
            "tag_name": r["name"],
            "color": r["color"],
            "is_system": bool(r["is_system"]),
            "assigned_by": r["assigned_by"],
        })
    return result
