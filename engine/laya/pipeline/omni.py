# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OMNI pipeline — maintain a rolling cross-platform summary.

Incremental updates append to the "recent" section without LLM calls.
Scheduled resynthesis uses the LLM to compress layers progressively.

Delta storage: incremental snapshots store only the diff (added/fused items
+ new card_ids). Resynthesis snapshots store the full structure and serve as
base checkpoints. Reconstruction chains deltas from the nearest base.

Omni's first rule: THE MODEL DECIDES WHAT IS IN ATTENTION. Code shapes the
model's inputs (which snapshot it sees, which cards, the live state of prior
items), validates its output (schema, empty/echo results) and prunes subjects
whose cards are all terminal — it never adds an item to attention, promotes one
into it by priority, or restores one the model left out. If attention is wrong,
the prompt is wrong (llm/prompts/omni.py); fix it there.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call, log_to_audit
from laya.models.card_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES
from laya.llm.prompts.omni import (
    DENSITY_PRESETS,
    build_omni_repair_messages,
    build_omni_resynthesis_messages,
    get_omni_json_schema,
)
from laya.models.omni import OmniItem, OmniSection, OmniSnapshot, OmniStats
from laya.pipeline.omni_change import (
    compute_incremental_change_summary,
    compute_resynthesis_change_summary,
    decorate_item_keys,
)

log = structlog.get_logger()

# Max new cards folded into a single resynthesis LLM call. A full run can fetch
# up to fetch_cap (~100-150) cards; combined with the current snapshot that
# exceeds a local model's usable window, and the omni JSON schema is unforgiving
# — a truncated response yields NO parsed output, losing the whole batch AND
# handing the next run an even bigger backlog (the failure spiral flagged in the
# Jul-2026 review, P6-13). We fold new cards in chunks of this size across
# sequential smaller calls instead. ~30-50 is the sweet spot: strictly smaller
# calls that local models can complete, at the cost of more of them.
_RESYNTH_CHUNK_SIZE = 40

# ---------------------------------------------------------------------------
# In-memory cache for the latest reconstructed snapshot per space.
# Populated on every write, avoids delta chain reconstruction on hot reads.
# ---------------------------------------------------------------------------
_latest_cache: dict[str, dict] = {}

_VALID_OMNI_PRIORITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def _is_degenerate_sections(sections: list[dict]) -> bool:
    """Detect a placeholder/skeleton resynthesis result rather than real content.

    A local model under load (e.g. flooded because the concurrency cap wasn't
    honored) can return every field as a literal '...' — observed: all items with
    text='...' and priority='...', plus a hallucinated extra section. Because
    resynthesis carries the snapshot FORWARD, storing such a result poisons the
    base and EVERY later incremental inherits it, so the whole Omni page renders
    as '...' until a good full snapshot replaces it.

    We use this to (a) reject a degenerate result before storing it — treating it
    like a failed synthesis so the last good snapshot stays — and (b) refuse to
    feed an already-poisoned snapshot back to the model on the next run, so it
    regenerates fresh instead of echoing the '...'.
    """
    items = [it for s in sections for it in s.get("items", [])]
    if not items:
        return False
    bad = 0
    for it in items:
        text = (it.get("text") or "").strip()
        prio = it.get("priority")
        if text in ("", "...", "…") or (prio is not None and prio not in _VALID_OMNI_PRIORITIES):
            bad += 1
    # Half or more of the items being placeholders is unmistakable — a healthy
    # synthesis has ~zero. A ratio (not "all") tolerates one odd item.
    return bad >= len(items) * 0.5


# ---------------------------------------------------------------------------
# Delta helpers
# ---------------------------------------------------------------------------


def _compute_delta(
    old_items: list[dict],
    new_items: list[dict],
) -> dict:
    """Compute the delta between old and new recent section items.

    Returns a dict with:
      - added_items: items that are entirely new (no entity match in old)
      - fused_updates: items that existed but were modified (keyed by entity_id)
    """
    old_by_entity: dict[str, dict] = {}
    old_card_set: set[str] = set()
    for item in old_items:
        eid = item.get("entity_id")
        if eid:
            old_by_entity[eid] = item
        for cid in item.get("source_cards", []):
            old_card_set.add(cid)

    added_items: list[dict] = []
    fused_updates: dict[str, dict] = {}

    for item in new_items:
        eid = item.get("entity_id")
        if eid and eid in old_by_entity:
            old = old_by_entity[eid]
            # Check if anything changed
            if (
                item.get("text") != old.get("text")
                or item.get("source_cards") != old.get("source_cards")
                or item.get("platforms") != old.get("platforms")
                or item.get("priority") != old.get("priority")
            ):
                fused_updates[eid] = {
                    "text": item["text"],
                    "source_cards": item.get("source_cards", []),
                    "platforms": item.get("platforms", []),
                    "priority": item.get("priority", "MEDIUM"),
                }
        else:
            # Check it's genuinely new (not already present in old by card ID)
            item_cards = set(item.get("source_cards", []))
            if not item_cards.issubset(old_card_set):
                added_items.append(item)

    return {
        "added_items": added_items,
        "fused_updates": fused_updates,
    }


def _apply_delta(content: dict, delta: dict) -> dict:
    """Apply a delta to a full content snapshot, mutating in place."""
    sections = content.get("sections", [])

    # Find the "recent" section
    recent_section = None
    for section in sections:
        if section.get("type") == "recent":
            recent_section = section
            break

    if recent_section is None:
        recent_section = {"type": "recent", "label": None, "items": []}
        sections.append(recent_section)

    # Apply fused_updates — match by entity_id, update fields
    for entity_id, updates in delta.get("fused_updates", {}).items():
        for item in recent_section.get("items", []):
            if item.get("entity_id") == entity_id:
                item.update(updates)
                break

    # Append added_items
    recent_section.setdefault("items", []).extend(delta.get("added_items", []))

    # Apply bookmark overrides
    for source_card_id, bookmarked in delta.get("bookmark_overrides", {}).items():
        for section in sections:
            for item in section.get("items", []):
                cards = item.get("source_cards", [])
                if cards and cards[0] == source_card_id:
                    item["bookmarked"] = bookmarked

    content["sections"] = sections
    return content


async def _find_base_version(db, space_id: str, current_version: int) -> int | None:
    """Find the version of the nearest base (non-delta) snapshot."""
    rows = await db.execute_fetchall(
        """SELECT version FROM omni_snapshots
           WHERE space_id = ? AND is_delta = 0 AND version <= ?
           ORDER BY version DESC LIMIT 1""",
        (space_id, current_version),
    )
    return rows[0]["version"] if rows else None


async def _load_full_snapshot(
    db, space_id: str, version: int | None = None
) -> tuple[dict | None, int, list[str], dict]:
    """Load a fully reconstructed snapshot, handling delta chains.

    For the latest version (version=None), checks the in-memory cache first.

    Returns (content_dict, version_number, card_ids_list, metadata_dict).
    metadata_dict contains snapshot_id, generated_at, snapshot_type.
    """
    # Cache hit for latest
    if version is None and space_id in _latest_cache:
        c = _latest_cache[space_id]
        # Deep-copy so a caller that mutates the returned content (e.g.
        # _append_to_recent appending recent items) can't corrupt the cached
        # snapshot before the DB commit. A failed commit would otherwise leave
        # the cache serving phantom state and re-processing would double-append
        # (review §2 pipeline / §4). Installs into the cache stay post-commit.
        return (
            copy.deepcopy(c["content"]),
            c["version"],
            list(c["card_ids"]),
            dict(c.get("meta", {})),
        )

    # Load the target row
    if version is not None:
        rows = await db.execute_fetchall(
            """SELECT snapshot_id, version, generated_at, snapshot_type,
                      content_json, card_ids, is_delta, base_version
               FROM omni_snapshots
               WHERE space_id = ? AND version = ?""",
            (space_id, version),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT snapshot_id, version, generated_at, snapshot_type,
                      content_json, card_ids, is_delta, base_version
               FROM omni_snapshots
               WHERE space_id = ?
               ORDER BY version DESC LIMIT 1""",
            (space_id,),
        )

    if not rows:
        return None, 0, [], {}

    row = rows[0]
    meta = {
        "snapshot_id": row["snapshot_id"],
        "generated_at": row["generated_at"],
        "snapshot_type": row["snapshot_type"],
    }

    if not row["is_delta"]:
        # Full snapshot — return directly
        content = json.loads(row["content_json"])
        card_ids = json.loads(row["card_ids"])
        return content, row["version"], card_ids, meta

    # Delta snapshot — reconstruct from base
    target_version = row["version"]
    base_version = row["base_version"]

    if base_version is None:
        base_version = await _find_base_version(db, space_id, target_version)

    if base_version is None:
        log.warning("omni_delta_no_base", space_id=space_id, version=target_version)
        return None, 0, [], {}

    # Load base snapshot
    base_rows = await db.execute_fetchall(
        """SELECT content_json, card_ids FROM omni_snapshots
           WHERE space_id = ? AND version = ? AND is_delta = 0""",
        (space_id, base_version),
    )

    if not base_rows:
        base_rows = await db.execute_fetchall(
            """SELECT content_json, card_ids, version FROM omni_snapshots
               WHERE space_id = ? AND is_delta = 0 AND version < ?
               ORDER BY version DESC LIMIT 1""",
            (space_id, target_version),
        )
        if not base_rows:
            log.warning("omni_delta_base_missing", space_id=space_id, base=base_version)
            return None, 0, [], {}
        base_version = base_rows[0]["version"]

    content = json.loads(base_rows[0]["content_json"])
    card_ids = json.loads(base_rows[0]["card_ids"])

    # Load all deltas from base+1 to target, in order
    delta_rows = await db.execute_fetchall(
        """SELECT version, content_json, card_ids FROM omni_snapshots
           WHERE space_id = ? AND is_delta = 1
             AND version > ? AND version <= ?
           ORDER BY version ASC""",
        (space_id, base_version, target_version),
    )

    for delta_row in delta_rows:
        delta = json.loads(delta_row["content_json"])
        delta_card_ids = json.loads(delta_row["card_ids"])
        content = _apply_delta(content, delta)
        card_ids = card_ids + [cid for cid in delta_card_ids if cid not in card_ids]

    return content, target_version, card_ids, meta

# ---------------------------------------------------------------------------
# Queue processor — polls omni_queue table instead of in-memory list.
# Cards are enqueued by emit.py in the same transaction as the card persist,
# so they survive engine crashes. During resynthesis the processor pauses
# to avoid the race where incremental updates get overwritten.
# ---------------------------------------------------------------------------
_POLL_INTERVAL_SECONDS = 10
_queue_task: asyncio.Task | None = None

# Per-space gate: when a space has an active resynthesis, its asyncio.Event
# is *cleared* (blocking). When resynthesis finishes it is *set* (unblocked).
_resynthesis_gates: dict[str, asyncio.Event] = {}


def _get_gate(space_id: str) -> asyncio.Event:
    """Get or create the resynthesis gate for a space (default: open)."""
    if space_id not in _resynthesis_gates:
        ev = asyncio.Event()
        ev.set()  # open by default — no resynthesis running
        _resynthesis_gates[space_id] = ev
    return _resynthesis_gates[space_id]


def start_omni_processor() -> None:
    """Start the background queue processor. Called once at startup."""
    global _queue_task
    if _queue_task is not None and not _queue_task.done():
        return
    from laya.tasks import create_task as create_tracked_task
    _queue_task = create_tracked_task(_queue_loop(), name="omni_queue_processor")
    log.info("omni_queue_processor_started")


def stop_omni_processor() -> None:
    """Stop the background queue processor. Called on shutdown."""
    global _queue_task
    if _queue_task is not None:
        _queue_task.cancel()
        _queue_task = None
        log.info("omni_queue_processor_stopped")


async def _queue_loop() -> None:
    """Poll omni_queue every POLL_INTERVAL_SECONDS and process batches."""
    while True:
        try:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            await _process_queue()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("omni_queue_loop_error", error=str(e))


async def _process_queue() -> None:
    """Read pending cards from omni_queue, group by space, and append.

    For spaces with an active resynthesis, cards are left in the queue
    and picked up on the next poll (after resynthesis completes).
    """
    settings = load_settings()
    if not settings.get("omni", {}).get("enabled", True):
        return

    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT oq.card_id, oq.space_id,
                  ac.header, ac.summary, ac.priority,
                  ac.entity_id, e.source_platform
           FROM omni_queue oq
           JOIN action_cards ac ON oq.card_id = ac.card_id
           LEFT JOIN events e ON ac.event_id = e.event_id
           ORDER BY oq.created_at ASC
           LIMIT 200"""
    )

    if not rows:
        return

    # Group by space_id; skip spaces with active resynthesis
    by_space: dict[str, list[dict]] = {}
    skipped_ids: list[str] = []
    for row in rows:
        sid = row["space_id"]
        gate = _get_gate(sid)
        if not gate.is_set():
            # Resynthesis running for this space — leave in queue
            skipped_ids.append(row["card_id"])
            continue
        by_space.setdefault(sid, []).append({
            "card_id": row["card_id"],
            "card_header": row["header"],
            "card_summary": row["summary"],
            "card_priority": row["priority"],
            "source_platform": row["source_platform"] or "unknown",
            "space_id": sid,
            "entity_id": row["entity_id"],
        })

    if skipped_ids:
        log.debug("omni_queue_skipped_resynthesis", count=len(skipped_ids))

    for space_id, space_cards in by_space.items():
        try:
            await _append_to_recent(space_cards)
            # Delete processed rows from the queue
            processed_ids = [c["card_id"] for c in space_cards]
            placeholders = ",".join("?" for _ in processed_ids)
            await db.execute(
                f"DELETE FROM omni_queue WHERE card_id IN ({placeholders})",
                processed_ids,
            )
            await db.commit()
        except Exception as e:
            log.error("omni_incremental_update_failed", space_id=space_id, error=str(e))


async def _append_to_recent(cards: list[dict]) -> None:
    """Append cards to the 'recent' section of the latest snapshot.

    No LLM call — purely structured data manipulation.
    """
    db = await get_db()

    # Group cards by space_id
    by_space: dict[str, list[dict]] = {}
    for card in cards:
        sid = card.get("space_id", "default")
        by_space.setdefault(sid, []).append(card)

    for space_id, space_cards in by_space.items():
        # Load latest snapshot (reconstructed if delta chain)
        content, version, existing_card_ids, _meta = await _load_full_snapshot(db, space_id)

        is_first = content is None
        if is_first:
            # First snapshot for this space — create skeleton as full base
            version = 0
            content = {
                "sections": [
                    {"type": "attention", "label": None, "items": []},
                    {"type": "recent", "label": None, "items": []},
                    {"type": "period", "label": None, "items": []},
                    {"type": "milestone", "label": None, "items": []},
                ]
            }
            existing_card_ids = []

        # Find the "recent" section
        recent_section = None
        for section in content.get("sections", []):
            if section.get("type") == "recent":
                recent_section = section
                break

        if recent_section is None:
            recent_section = {"type": "recent", "label": None, "items": []}
            content.setdefault("sections", []).append(recent_section)

        # Snapshot old items for delta computation
        old_recent_items = copy.deepcopy(recent_section.get("items", []))

        # Build an index of existing recent items by entity_id for fusion.
        # entity_id is stored on each item so we can match incoming cards
        # against items already in the recent section.
        entity_index: dict[str, int] = {}
        for idx, existing_item in enumerate(recent_section.get("items", [])):
            eid = existing_item.get("entity_id")
            if eid:
                entity_index[eid] = idx

        # Append new cards — fuse with existing items when same entity
        new_card_ids = []
        _PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for card in space_cards:
            cid = card["card_id"]
            if cid in existing_card_ids:
                continue

            card_entity_id = card.get("entity_id")
            platform = card.get("source_platform", "unknown")
            priority = card.get("card_priority", "MEDIUM")

            # Check if an existing recent item covers the same entity
            if card_entity_id and card_entity_id in entity_index:
                # Fuse: update existing item instead of creating a new one
                existing_idx = entity_index[card_entity_id]
                existing_item = recent_section["items"][existing_idx]

                # Use the latest card's text (most recent = most complete picture)
                existing_item["text"] = f"{card['card_header']} — {card['card_summary']}"

                # Merge source_cards list
                if cid not in existing_item.get("source_cards", []):
                    existing_item.setdefault("source_cards", []).append(cid)

                # Merge platforms (deduplicate)
                if platform not in existing_item.get("platforms", []):
                    existing_item.setdefault("platforms", []).append(platform)

                # Escalate priority (keep the highest)
                old_rank = _PRIORITY_RANK.get(existing_item.get("priority", "MEDIUM"), 2)
                new_rank = _PRIORITY_RANK.get(priority, 2)
                if new_rank < old_rank:
                    existing_item["priority"] = priority
            else:
                # New entity — create a fresh item
                item = {
                    "text": f"{card['card_header']} — {card['card_summary']}",
                    "source_cards": [cid],
                    "platforms": [platform],
                    "priority": priority,
                    "pinned": False,
                    "bookmarked": False,
                    "entity_id": card_entity_id,
                }
                recent_section["items"].append(item)
                if card_entity_id:
                    entity_index[card_entity_id] = len(recent_section["items"]) - 1

            new_card_ids.append(cid)

        if not new_card_ids:
            continue

        all_card_ids = existing_card_ids + new_card_ids
        now = db_now()

        # Keys are stamped on every write so the drill-down link and the
        # changelog name the same item; recomputing on read yields the same
        # value, so pre-072 snapshots stay addressable too.
        decorate_item_keys(content.get("sections", []))

        # Create a new incremental snapshot (version++)
        new_snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
        new_version = version + 1

        if is_first:
            # First snapshot ever — store as full base (no delta possible).
            # Every item is new, so the change summary is the whole recent list.
            change_summary = compute_incremental_change_summary(
                recent_section.get("items", []), "recent"
            )
            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed, created_at,
                    is_delta, base_version, change_summary_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_snapshot_id, space_id, new_version, now, "incremental",
                    json.dumps(content), json.dumps(all_card_ids),
                    len(all_card_ids), now, 0, None,
                    json.dumps(change_summary),
                ),
            )
        else:
            # Compute delta from old state and store only the diff
            delta = _compute_delta(old_recent_items, recent_section.get("items", []))
            base_ver = await _find_base_version(db, space_id, version)
            change_summary = compute_incremental_change_summary(
                delta.get("added_items", []), "recent"
            )

            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed, created_at,
                    is_delta, base_version, change_summary_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_snapshot_id, space_id, new_version, now, "incremental",
                    json.dumps(delta), json.dumps(new_card_ids),
                    len(all_card_ids), now, 1, base_ver,
                    json.dumps(change_summary),
                ),
            )

        await db.commit()

        # Update in-memory cache with full reconstructed state
        _latest_cache[space_id] = {
            "content": content,
            "version": new_version,
            "card_ids": all_card_ids,
            "meta": {
                "snapshot_id": new_snapshot_id,
                "generated_at": now,
                "snapshot_type": "incremental",
            },
        }

        # Broadcast update
        await manager.broadcast({
            "type": "omni_updated",
            "payload": {
                "space_id": space_id,
                "version": new_version,
                "snapshot_type": "incremental",
                "new_items": len(new_card_ids),
            },
        })

        log.info(
            "omni_incremental_update",
            space_id=space_id,
            version=new_version,
            new_items=len(new_card_ids),
        )


# ---------------------------------------------------------------------------
# Full resynthesis (LLM-powered)
# ---------------------------------------------------------------------------

# `_TERMINAL_STATUSES` is imported from card_lifecycle at module top (single source
# of truth): a subject in one of these statuses is resolved and leaves the attention
# section. No local mirror — that previously risked silent divergence.
_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


async def _fetch_card_meta(db, card_ids: list[str]) -> dict[str, dict]:
    """Fetch live per-card state for a set of card_ids.

    Returns ``{card_id: {status, priority, entity_id, created_at, resolved_at,
    source_platform}}``. The last three feed the API's per-item ``live``
    decoration (item age, resolution timestamps, the platform-mix instrument);
    the pipeline itself only reads status/priority/entity_id.
    """
    if not card_ids:
        return {}
    unique_ids = list(dict.fromkeys(card_ids))
    placeholders = ",".join("?" for _ in unique_ids)
    rows = await db.execute_fetchall(
        f"""SELECT ac.card_id, ac.status, ac.priority, ac.entity_id,
                   ac.created_at, ac.resolved_at, e.source_platform
            FROM action_cards ac
            LEFT JOIN events e ON ac.event_id = e.event_id
            WHERE ac.card_id IN ({placeholders})""",
        unique_ids,
    )
    return {
        r["card_id"]: {
            "status": r["status"],
            "priority": r["priority"],
            "entity_id": r["entity_id"],
            "created_at": r["created_at"],
            "resolved_at": r["resolved_at"],
            "source_platform": r["source_platform"] or "unknown",
        }
        for r in rows
    }


def _item_source_cards(item: dict) -> list[str]:
    return [c for c in item.get("source_cards", []) if c]


def _live_max_priority(card_ids: list[str], meta: dict[str, dict]) -> str | None:
    """Highest priority among non-terminal source cards, or None if all resolved/unknown."""
    best: str | None = None
    best_rank = 99
    for cid in card_ids:
        m = meta.get(cid)
        if not m or m.get("status") in _TERMINAL_STATUSES:
            continue
        rank = _PRIORITY_ORDER.get(m.get("priority", "MEDIUM"), 2)
        if rank < best_rank:
            best_rank = rank
            best = m.get("priority", "MEDIUM")
    return best


def _all_resolved(card_ids: list[str], meta: dict[str, dict]) -> bool:
    """True if there is at least one known source card and ALL are terminal."""
    known = [meta[cid] for cid in card_ids if cid in meta]
    if not known:
        return False
    return all(m.get("status") in _TERMINAL_STATUSES for m in known)


# SQLite's default bound-parameter limit is 999; chunk id lists well below it.
_SQL_IN_CHUNK = 500


async def _fetch_omni_cards(db, where: str, params: tuple, limit: int | None = None) -> list:
    """Card rows in the shape resynthesis folds into the prompt, newest first."""
    sql = f"""SELECT ac.card_id, ac.header, ac.summary, ac.priority, ac.persona,
                     ac.status, ac.user_feedback, ac.category, ac.entity_id,
                     ac.created_at, e.source_platform, e.actor_name
              FROM action_cards ac
              LEFT JOIN events e ON ac.event_id = e.event_id
              WHERE {where}
              ORDER BY ac.created_at DESC"""
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    return await db.execute_fetchall(sql, params)


_EXIT_REASONS = {
    "resolved", "superseded", "obsolete", "handed_off", "user_acted", "expired",
    "deprioritised",
}


def _valid_attention_exits(raw) -> list[dict]:
    """The model's `attention_exits`, reduced to well-formed entries.

    The model alone decides what is in attention (Omni's first rule — see the
    module docstring); these entries are its stated reasons for each subject it
    took OUT, recorded in the change summary so the changelog can say why a
    line left ("superseded: build passed on PR-748"). Nothing acts on them. An
    entry without entity_ids names no subject and is dropped; an unknown reason
    is kept as "other" rather than discarded, since the note still explains it.
    """
    exits: list[dict] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        eids = [e for e in (entry.get("entity_ids") or []) if isinstance(e, str) and e]
        if not eids:
            continue
        reason = str(entry.get("reason") or "").strip().lower().replace("-", "_")
        exits.append({
            "entity_ids": eids,
            "reason": reason if reason in _EXIT_REASONS else "other",
            "note": str(entry.get("note") or "").strip(),
        })
    return exits


def _new_cards_cited(sections: list[dict], new_card_ids: set[str]) -> set[str]:
    """Ids of the new cards that some output item's source_cards names."""
    return {
        cid
        for section in sections or []
        for item in section.get("items", []) or []
        for cid in _item_source_cards(item)
        if cid in new_card_ids
    }


def _is_echo(result_sections: list[dict], prior_sections: list[dict]) -> bool:
    """True when every output item is a prior item re-emitted (same text or
    same source cards). This is the original failure mode: the model returning
    last run's aggregates and discarding the new cards."""
    prior_texts: set[str] = set()
    prior_cards: set[frozenset[str]] = set()
    for section in prior_sections or []:
        for item in section.get("items", []) or []:
            prior_texts.add((item.get("text") or "").strip().lower())
            cards = frozenset(_item_source_cards(item))
            if cards:
                prior_cards.add(cards)
    out = [it for s_ in result_sections or [] for it in s_.get("items", []) or []]
    if not out:
        return False
    return all(
        (it.get("text") or "").strip().lower() in prior_texts
        or (frozenset(_item_source_cards(it)) in prior_cards)
        for it in out
    )


def _output_digest(sections: list[dict], new_card_ids: set[str]) -> list[dict]:
    """A compact, human-readable record of what the model returned.

    Kept in the audit row of a rejected run, because the output itself is
    discarded and the user otherwise has no way to see WHAT was rejected.
    """
    digest: list[dict] = []
    for section in sections or []:
        for item in section.get("items", []) or []:
            cards = _item_source_cards(item)
            digest.append({
                "section": section.get("type"),
                "priority": item.get("priority"),
                "text": (item.get("text") or "")[:160],
                "source_cards": len(cards),
                "new_cards_cited": sum(1 for c in cards if c in new_card_ids),
            })
    return digest


async def _audit_resynthesis(
    space_id: str,
    *,
    outcome: str,
    summary: str,
    metadata: dict | None = None,
) -> None:
    """One audit_log row for a resynthesis run that did NOT store a snapshot.

    The LLM call already writes its own row (step omni_resynthesis, success =
    the call returned), which reads as a successful run even when the result
    was then rejected by a guard or the call failed. This row explains that to
    the user: the `error` column carries the one-line explanation the audit
    viewer shows, `metadata` the details (reason code, counts, and for a
    rejection a digest of the discarded output). Stored runs write nothing
    here — the snapshot itself is their record.
    """
    await log_to_audit(
        event_id=None,
        card_id=None,
        step="omni_resynthesis_outcome",
        model="",
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        success=False,
        error=summary,
        metadata={"space_id": space_id, "outcome": outcome, "summary": summary,
                  **(metadata or {})},
    )


async def run_omni_resynthesis(
    space_id: str | None = None,
    snapshot_type: str = "scheduled",
) -> list[str]:
    """Run a full Omni resynthesis for one or all spaces.

    This is the expensive operation — it calls the LLM to compress the
    recent layer into period aggregates, fold old periods into milestones,
    and surface attention items.

    Args:
        space_id: Specific space to resynthesize, or None for all spaces.
        snapshot_type: "scheduled" (EOD), "rolling" (interval/threshold), or "manual".

    Returns:
        List of snapshot_ids created.
    """
    settings = load_settings()
    omni_cfg = settings.get("omni", {})
    density = omni_cfg.get("density", "compact")
    try:
        event_threshold = int(omni_cfg.get("event_threshold", 50))
    except (TypeError, ValueError):
        event_threshold = 50
    event_threshold = max(0, min(100, event_threshold))

    db = await get_db()

    # Determine which spaces to process
    if space_id:
        space_ids = [space_id]
    else:
        space_rows = await db.execute_fetchall("SELECT space_id FROM spaces")
        space_ids = [row["space_id"] for row in space_rows] if space_rows else ["default"]
        # Ensure default is always included
        if "default" not in space_ids:
            space_ids.append("default")

    created_ids = []

    for sid in space_ids:
        try:
            snapshot_id = await _resynthesize_space(db, sid, density, snapshot_type, event_threshold)
            if snapshot_id:
                created_ids.append(snapshot_id)
        except Exception as e:
            log.error("omni_resynthesis_failed", space_id=sid, error=str(e))

    return created_ids


async def _resynthesize_space(
    db,
    space_id: str,
    density: str,
    snapshot_type: str = "scheduled",
    event_threshold: int = 50,
) -> str | None:
    """Resynthesize Omni for a single space.

    Gates the queue processor for this space during the LLM call so that
    incremental updates don't race with the resynthesis snapshot write.
    Cards that arrive during resynthesis stay in omni_queue and are
    processed on the next poll after the gate reopens.
    """
    gate = _get_gate(space_id)

    # 1. Load latest snapshot (reconstructed if delta chain)
    current_snapshot, current_version, existing_card_ids, _meta = await _load_full_snapshot(db, space_id)

    # The pre-fold sections, kept separately because `current_snapshot` may be
    # discarded below (degenerate recovery) and is fed forward through the LLM
    # calls. The change summary must diff against what the user was ACTUALLY
    # looking at, so it reads this copy.
    prior_sections = copy.deepcopy((current_snapshot or {}).get("sections", []))

    # Recovery: if the last snapshot is itself degenerate (a prior bad synthesis
    # poisoned the chain), don't feed it back to the model — that just makes it
    # echo the '...'. Drop it so this run regenerates real content from scratch;
    # item_states/resolved-pruning below are skipped when there's no snapshot.
    if current_snapshot and _is_degenerate_sections(current_snapshot.get("sections", [])):
        log.warning(
            "omni_resynthesis_discarding_degenerate_snapshot",
            space_id=space_id, version=current_version,
        )
        current_snapshot = None

    # 1b. The LLM is handed the last BASE snapshot, not the reconstruction.
    # Every incremental delta since that base appended one raw item per card to
    # `recent`; those same cards are the ones this run folds (they are listed in
    # the new-card block with full metadata), so in the reconstruction they are
    # present twice. Worse, a `recent` list far over the density cap makes a
    # small model keep the first N items — its own previous aggregates — and
    # discard the tail, i.e. exactly the new cards. The base's `recent` (last
    # run's aggregates) is handed over separately as period/milestone material.
    # `prior_sections` keeps the reconstruction: the change summary must diff
    # against what the user was actually looking at.
    base_card_ids: list[str] = list(existing_card_ids)
    if current_snapshot is not None:
        base_version = await _find_base_version(db, space_id, current_version)
        if base_version is not None and base_version != current_version:
            base_content, _, base_ids, base_meta = await _load_full_snapshot(
                db, space_id, base_version
            )
            if base_content is not None:
                current_snapshot = base_content
                base_card_ids = list(base_ids)
                _meta = base_meta
    # The very first snapshot of a space is written by the incremental path as a
    # base whose `recent` is raw per-card items; those cards are all still in the
    # new-card window, so there are no prior aggregates to offer.
    prior_recent_items: list[dict] = []
    if current_snapshot is not None and _meta.get("snapshot_type") != "incremental":
        prior_recent_items = [
            item
            for s in current_snapshot.get("sections", [])
            if s.get("type") == "recent"
            for item in s.get("items", [])
        ]

    # 2. Load pinned items
    pin_rows = await db.execute_fetchall(
        "SELECT item_text, source_card_ids, platforms FROM omni_pins WHERE space_id = ?",
        (space_id,),
    )
    pinned_items = [
        {
            "item_text": row["item_text"],
            "source_card_ids": json.loads(row["source_card_ids"]),
            "platforms": json.loads(row["platforms"]),
        }
        for row in pin_rows
    ]

    # 3. Query recent cards (since last successful resynthesis)
    last_synth_row = await db.execute_fetchall(
        """SELECT generated_at FROM omni_snapshots
           WHERE space_id = ? AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )

    # generated_at and resolved_at are both stored in canonical DB format now
    # (space-separated UTC — see laya/db/timeutil.py), so one space-format `since`
    # bound compares correctly against both created_at and resolved_at.
    raw = last_synth_row[0]["generated_at"] if last_synth_row else None
    since = raw if raw else "2000-01-01 00:00:00"

    # Fetch cap scales with event_threshold so users who tolerate larger
    # per-run batches get proportional headroom for failure recovery. Floor
    # is 100 (also applies when threshold is disabled).
    fetch_cap = max(100, 3 * event_threshold) if event_threshold > 0 else 100

    card_rows = list(await _fetch_omni_cards(
        db, "ac.space_id = ? AND ac.created_at > ?", (space_id, since), limit=fetch_cap,
    ))
    since_count = len(card_rows)

    # Cards the incremental path folded in since the last base that the `since`
    # query cannot see. `created_at` is the EVENT's platform time, not ingest
    # time, so a card ingested after the last synthesis with an older event time
    # only exists in the delta chain — and after this run advances `since` it
    # would never match the window again. These are never truncated by the
    # fetch cap for the same reason.
    base_card_set = set(base_card_ids)
    fetched = {row["card_id"] for row in card_rows}
    delta_only_ids = [
        cid for cid in existing_card_ids if cid not in base_card_set and cid not in fetched
    ]
    for i in range(0, len(delta_only_ids), _SQL_IN_CHUNK):
        chunk_ids = delta_only_ids[i:i + _SQL_IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk_ids)
        card_rows.extend(await _fetch_omni_cards(
            db, f"ac.space_id = ? AND ac.card_id IN ({placeholders})", (space_id, *chunk_ids),
        ))
    if delta_only_ids:
        # Chunking below folds oldest-first, so the union must keep newest-first.
        card_rows.sort(key=lambda r: r["created_at"] or "", reverse=True)

    new_cards = [
        {
            "card_id": row["card_id"],
            "header": row["header"],
            "summary": row["summary"],
            "priority": row["priority"],
            "source_platform": row["source_platform"] or "unknown",
            "user_feedback": row["user_feedback"],
            "status": row["status"],
            "entity_id": row["entity_id"],
        }
        for row in card_rows
    ]

    # Enrich cards with tags for the LLM prompt
    from laya.pipeline.tags import batch_load_tags
    omni_card_ids = [c["card_id"] for c in new_cards]
    tags_map = await batch_load_tags(omni_card_ids)
    for c in new_cards:
        card_tag_entries = tags_map.get(("card", c["card_id"]), [])
        c["tags"] = ", ".join(t["tag_name"] for t in card_tag_entries) if card_tag_entries else ""

    # 4. Separate user-acted cards (for higher weight in prompt)
    acted_cards = [
        c for c in new_cards
        if c.get("user_feedback") or c.get("status") in ("done", "dismissed", "archived")
    ]

    # If no new cards, skip — nothing has changed since the last synthesis.
    # (No snapshot + no cards = first run with nothing to process;
    #  existing snapshot + no cards = redundant LLM call with identical input.)
    if not new_cards:
        log.info("omni_resynthesis_skipped_no_new_cards", space_id=space_id)
        return None

    # Visibility: warn when the fetch cap is saturated. Under the default
    # trigger config this should be rare; if it fires repeatedly, check for
    # recent LLM failures or misconfigured triggers before trusting the
    # summary (cards beyond the window are silently dropped from the LLM
    # input, though they remain in the incremental snapshot).
    if since_count >= fetch_cap:
        log.warning(
            "omni_resynthesis_cards_saturated",
            space_id=space_id,
            cap=fetch_cap,
            event_threshold=event_threshold,
            since=since,
        )

    # 4b. Derive the live state of the CURRENT snapshot's items so the LLM can
    # drop subjects that have since resolved or de-escalated. Resynthesis is
    # otherwise blind to status-only transitions of cards created before `since`
    # (they don't reappear in `new_cards`), so without this an attention item
    # sticks forever. We compute state from each item's source_cards because the
    # auto-resolution path (emit.py) transitions the very sibling cards embedded
    # in the aggregate to a terminal status.
    item_states: list[dict] = []
    if current_snapshot:
        snapshot_card_ids: list[str] = []
        for section in current_snapshot.get("sections", []):
            if section.get("type") not in ("attention", "recent"):
                continue
            for item in section.get("items", []):
                snapshot_card_ids.extend(_item_source_cards(item))
        snap_meta = await _fetch_card_meta(db, snapshot_card_ids)
        for section in current_snapshot.get("sections", []):
            if section.get("type") not in ("attention", "recent"):
                continue
            for item in section.get("items", []):
                cids = _item_source_cards(item)
                if not cids:
                    continue
                item_states.append({
                    "text": item.get("text", ""),
                    "all_resolved": _all_resolved(cids, snap_meta),
                    "live_max_priority": _live_max_priority(cids, snap_meta),
                })

    # 4c. Subjects that reached a terminal state since the last synthesis.
    resolved_rows = await db.execute_fetchall(
        """SELECT entity_id, header, status, resolved_at
           FROM action_cards
           WHERE space_id = ? AND resolved_at IS NOT NULL AND resolved_at > ?
             AND status IN ('done', 'dismissed', 'archived')
           ORDER BY resolved_at DESC LIMIT 100""",
        (space_id, since),
    )
    resolved_cards: list[dict] = []
    _seen_entities: set[str] = set()
    # entity_id → when that subject reached a terminal state. The changelog rail
    # renders "closed 14:22" beside a resolved line; the card that resolved a
    # subject is often NOT one of the aggregate's own source_cards (a merge event
    # mints a new card on the same entity), so the timestamp is keyed by entity.
    resolved_at_by_entity: dict[str, str] = {}
    for r in resolved_rows:
        eid = r["entity_id"]
        if eid and r["resolved_at"] and eid not in resolved_at_by_entity:
            resolved_at_by_entity[eid] = r["resolved_at"]
        if eid and eid in _seen_entities:
            continue  # dedupe by entity — one resolution line per subject
        if eid:
            _seen_entities.add(eid)
        resolved_cards.append({
            "entity_id": eid,
            "header": r["header"],
            "status": r["status"],
        })

    # 5. Fold the new cards into the snapshot across one or more LLM calls.
    #
    # Small batches run as a SINGLE call, byte-identical to the pre-P6-13 path.
    # Large bursts are folded in chunks of _RESYNTH_CHUNK_SIZE across sequential
    # calls, each folding one chunk into the evolving snapshot, so no single call
    # carries the whole burst (see the constant's comment for why that matters).
    #
    # Ordering: new_cards is newest-first (created_at DESC), so we fold the
    # OLDEST chunk first and the newest last — that way the freshest cards land
    # in the final call and dominate the "recent" section. Snapshot-relative
    # inputs (item_states / resolved_cards, which prune subjects that resolved
    # since the last synthesis) apply only to the FIRST fold; afterwards the
    # evolving snapshot already reflects them. Pins carry through every fold, and
    # each chunk brings its own acted cards.
    #
    # Failure handling is deliberately all-or-nothing: if any chunk fails to
    # parse we discard the whole run and return None WITHOUT storing, leaving
    # `since` unadvanced so every card returns next run. That avoids the plumbing
    # needed to store a partial fold without silently dropping the un-folded
    # cards (the fetch is purely `since`-driven — line ~724), and because each
    # chunk is small the retry almost always succeeds. The previous snapshot
    # stays on screen until it does.
    # Cards the model may cite: everything behind the prior snapshot plus the
    # new cards folded so far. The schema restricts source_cards to this set
    # and requires at least one per item (see get_omni_json_schema).
    citable_ids: set[str] = set(existing_card_ids)

    chunks = [
        new_cards[i:i + _RESYNTH_CHUNK_SIZE]
        for i in range(0, len(new_cards), _RESYNTH_CHUNK_SIZE)
    ]
    chunks.reverse()  # oldest chunk first, newest last

    # --- GATE: pause queue processing for this space during the LLM call(s) ---
    gate.clear()
    log.info("omni_resynthesis_gate_closed", space_id=space_id, chunks=len(chunks))

    # 6. Call LLM (once per chunk)
    try:
        folded_snapshot = current_snapshot
        result_sections: list[dict] = []
        attention_exits: list[dict] = []
        # The last fold's conversation, kept for the repair turn below.
        last_messages: list[dict] = []
        last_schema: dict = {}
        last_raw: str = ""
        exits_before_last: list[dict] = []
        for idx, chunk in enumerate(chunks):
            first = idx == 0
            citable_ids.update(c["card_id"] for c in chunk)
            schema = get_omni_json_schema(density, citable_ids)
            chunk_acted = [
                c for c in chunk
                if c.get("user_feedback")
                or c.get("status") in ("done", "dismissed", "archived")
            ]
            messages = build_omni_resynthesis_messages(
                current_snapshot=folded_snapshot,
                new_cards=chunk,
                acted_cards=chunk_acted,
                pinned_items=pinned_items,
                density=density,
                space_id=space_id,
                # Prune-resolved hints only make sense against the ORIGINAL
                # snapshot — apply them once, on the first fold. Likewise the
                # recent split: a later fold's `recent` was built from this
                # run's own cards and must be merged into, not set aside.
                item_states=item_states if first else [],
                resolved_cards=resolved_cards if first else [],
                prior_recent_items=prior_recent_items if first else None,
            )
            response = await llm_call(
                role="omni",
                messages=messages,
                response_schema=schema,
                step="omni_resynthesis",
                temperature=0.3,
                max_tokens=DEFAULT_MAX_TOKENS,
                space_id=space_id,
            )

            if not response.parsed:
                truncation_hint = " (response was truncated)" if response.truncated else ""
                raise ValueError(
                    f"LLM returned malformed JSON for Omni resynthesis "
                    f"chunk {idx + 1}/{len(chunks)}{truncation_hint} "
                    f"(output_tokens={response.output_tokens}, model={response.model})"
                )

            result_sections = response.parsed.get("sections", [])
            exits_before_last = list(attention_exits)
            attention_exits.extend(
                _valid_attention_exits(response.parsed.get("attention_exits"))
            )
            last_messages, last_schema = messages, schema
            last_raw = json.dumps(response.parsed)
            # Feed the fold forward WITHOUT the exits: a later chunk would read
            # them as part of the snapshot and could echo them as new exits.
            folded_snapshot = {
                k: v for k, v in response.parsed.items() if k != "attention_exits"
            }

    except Exception as e:
        log.error("omni_resynthesis_llm_failed", space_id=space_id, error=str(e))
        await _audit_resynthesis(
            space_id, outcome="llm_failed",
            summary=f"Resynthesis failed: the model call did not return usable output ({e})",
            metadata={"new_cards": len(new_cards)},
        )
        # Re-open the gate so queued cards resume processing
        gate.set()
        log.info("omni_resynthesis_gate_opened", space_id=space_id, reason="llm_failed")
        return None

    # Reject a degenerate ('...'-everywhere) result before it can be stored and
    # poison the forward-carried snapshot — see _is_degenerate_sections. Treat it
    # like a failed synthesis: keep the last good snapshot, retry next run.
    if _is_degenerate_sections(result_sections):
        log.warning(
            "omni_resynthesis_degenerate_result",
            space_id=space_id, items=sum(len(s.get("items", [])) for s in result_sections),
        )
        await _audit_resynthesis(
            space_id, outcome="degenerate_result",
            summary="Resynthesis rejected: the model returned placeholder text ('...') "
                    "instead of summaries. Previous snapshot kept; cards will be retried.",
            metadata={"new_cards": len(new_cards),
                      "output": _output_digest(result_sections, {c["card_id"] for c in new_cards})},
        )
        gate.set()
        log.info("omni_resynthesis_gate_opened", space_id=space_id, reason="degenerate_result")
        return None

    # Reject an all-empty result (zero items across every section) for the same
    # reason as the '...' skeleton above: it poisons the forward-carried snapshot.
    # _is_degenerate_sections deliberately EXEMPTS the no-items case (it targets
    # the placeholder-text variant), so the empty-collapse variant is caught here.
    # We only reach this point with new_cards non-empty (the `if not new_cards`
    # early-return above guarantees it), so a synthesis that folds real,
    # un-summarized cards into *nothing* is a model failure, not a legitimately
    # empty state — a mid-size local model can over-apply the prompt's "compress
    # everything / an empty array is allowed" guidance and emit zero items.
    # Storing it would wipe the recent items the incremental queue accumulated
    # AND feed the empty snapshot back into the next run, so every later
    # resynthesis stays empty (observed: high-volume space collapsing 13k→389
    # bytes over successive rolling runs). Treat it as a failed synthesis: keep
    # the last good snapshot, leave `since` unadvanced so these cards retry.
    # NOTE: checked BEFORE the resolved-attention prune below, so a result whose
    # items were legitimately dropped as resolved still stores (correctly empty)
    # while a model that returned nothing does not.
    if sum(len(s.get("items", [])) for s in result_sections) == 0:
        log.warning(
            "omni_resynthesis_empty_result",
            space_id=space_id, new_cards=len(new_cards),
        )
        await _audit_resynthesis(
            space_id, outcome="empty_result",
            summary=f"Resynthesis rejected: the model returned no items at all for "
                    f"{len(new_cards)} new cards. Previous snapshot kept; cards will be retried.",
            metadata={"new_cards": len(new_cards)},
        )
        gate.set()
        log.info("omni_resynthesis_gate_opened", space_id=space_id, reason="empty_result")
        return None

    # Reject a result that accounts for NONE of the new cards. A model that
    # re-emits the previous aggregates verbatim passes the empty-result guard
    # (it has items) yet has synthesized nothing; storing it advances `since`
    # and the un-folded cards are gone from the LLM path for good. Treated like
    # a failed synthesis: keep the last good snapshot, retry with the same cards
    # next run. A persistently uncooperative model turns this into a retry loop
    # with a growing backlog (surfaced by omni_resynthesis_cards_saturated),
    # which is preferred to silently losing the cards. Measured BEFORE the
    # resolved-attention prune in 7b: a new card echoed into attention and then
    # legitimately pruned as resolved still counts as covered.
    new_card_set = {c["card_id"] for c in new_cards}
    covered = _new_cards_cited(result_sections, new_card_set)
    repair_attempted = False
    repair_error: str | None = None
    if new_card_set and not covered:
        # The model cited none of the cards it was asked to fold. Two things
        # look like this: an echo of last run's aggregates (the new cards were
        # ignored) and a genuine synthesis whose citations were left off. The
        # pipeline cannot tell them apart and must not guess, so the model is
        # shown its own output and asked to finish the job — attach the ids or
        # fold the cards it skipped. One turn; the schema already limits
        # source_cards to real ids and requires one per item.
        repair_attempted = True
        log.warning(
            "omni_resynthesis_repair_turn",
            space_id=space_id, new_cards=len(new_card_set),
            echo=_is_echo(result_sections, prior_sections),
        )
        try:
            repaired = await llm_call(
                role="omni",
                messages=build_omni_repair_messages(last_messages, last_raw, len(new_card_set)),
                response_schema=last_schema,
                step="omni_resynthesis_repair",
                temperature=0.3,
                max_tokens=DEFAULT_MAX_TOKENS,
                space_id=space_id,
            )
            if repaired.parsed:
                result_sections = repaired.parsed.get("sections", [])
                attention_exits = exits_before_last + _valid_attention_exits(
                    repaired.parsed.get("attention_exits")
                )
                covered = _new_cards_cited(result_sections, new_card_set)
            else:
                repair_error = "malformed JSON" + (
                    " (response was truncated)" if repaired.truncated else ""
                )
        except Exception as e:  # noqa: BLE001 — the repair turn must not take the run down
            repair_error = str(e)

    log.info(
        "omni_resynthesis_coverage",
        space_id=space_id,
        covered=len(covered),
        new_cards=len(new_card_set),
        ratio=round(len(covered) / max(len(new_card_set), 1), 2),
        repair_attempted=repair_attempted,
    )
    if new_card_set and not covered:
        # Still nothing cited after the model was asked directly. Storing this
        # would advance the window and bury the cards; keep the previous
        # snapshot so they are offered again next run.
        echo = _is_echo(result_sections, prior_sections)
        log.warning(
            "omni_resynthesis_no_new_coverage",
            space_id=space_id, new_cards=len(new_card_set), echo=echo,
            repair_error=repair_error,
        )
        what = (
            "the model returned last run's aggregates unchanged" if echo
            else "the model's lines cite none of the new cards"
        )
        await _audit_resynthesis(
            space_id, outcome="no_new_coverage",
            summary=f"Resynthesis rejected: {what}, even after being asked once to attach "
                    f"the ids or fold the cards ({len(new_card_set)} new cards). Previous "
                    f"snapshot kept; the cards will be offered again next run.",
            metadata={"new_cards": len(new_card_set), "echo": echo,
                      "repair_attempted": repair_attempted, "repair_error": repair_error,
                      "output": _output_digest(result_sections, new_card_set)},
        )
        gate.set()
        log.info("omni_resynthesis_gate_opened", space_id=space_id, reason="no_new_coverage")
        return None

    # 7. Inject space_id into all items
    for section in result_sections:
        for item in section.get("items", []):
            item["space_id"] = space_id

    # 7b. Resolved-subject prune + entity_ids backfill.
    # The model decides what is in attention (Omni's first rule — module
    # docstring). The one deterministic exception is this prune: an attention
    # item whose source cards are ALL in a terminal status is dropped, because
    # a finished subject cannot need the user whatever the model says, and the
    # status-only transition that finished it is invisible in the new cards.
    # Nothing is ever ADDED to attention or moved into it by code. We also
    # backfill entity_ids from source_cards when the LLM left them empty, so the
    # NEXT resynthesis can correlate this subject reliably.
    output_card_ids: list[str] = []
    for section in result_sections:
        for item in section.get("items", []):
            output_card_ids.extend(_item_source_cards(item))
    # Prior cards are fetched in the same batch: the change summary has to decide
    # whether a line that VANISHED did so because its subjects resolved, and
    # those cards are by definition absent from the new output.
    for section in prior_sections:
        for item in section.get("items", []):
            output_card_ids.extend(_item_source_cards(item))
    out_meta = await _fetch_card_meta(db, output_card_ids)

    pruned_attention = 0
    for section in result_sections:
        is_attention = section.get("type") == "attention"
        kept_items = []
        for item in section.get("items", []):
            cids = _item_source_cards(item)
            if is_attention and _all_resolved(cids, out_meta):
                pruned_attention += 1
                continue  # every source subject resolved — drop from attention
            # Backfill entity_ids the LLM omitted, from the live card metadata.
            if not item.get("entity_ids"):
                eids = []
                for cid in cids:
                    eid = (out_meta.get(cid) or {}).get("entity_id")
                    if eid and eid not in eids:
                        eids.append(eid)
                item["entity_ids"] = eids
            kept_items.append(item)
        section["items"] = kept_items

    if pruned_attention:
        log.info("omni_attention_pruned", space_id=space_id, dropped=pruned_attention)

    # 7c. Stamp item keys, then diff this result against what the user was
    # looking at. Order matters: keys are computed from the FINAL entity_ids
    # (backfilled just above), and the diff must see the post-prune sections so a
    # resolved attention item reads as `resolved`, not as a silent disappearance.
    decorate_item_keys(result_sections)
    change_summary = compute_resynthesis_change_summary(
        prior_sections,
        result_sections,
        out_meta,
        _TERMINAL_STATUSES,
        resolved_at_by_entity,
        attention_exits,
    )

    # 8. Build stats
    cards_acted_count = len(acted_cards)
    total_items_before = sum(
        len(s.get("items", []))
        for s in (current_snapshot or {}).get("sections", [])
    ) + len(new_cards)
    total_items_after = sum(len(s.get("items", [])) for s in result_sections)
    compression = 1.0 - (total_items_after / max(total_items_before, 1))

    content = {
        "sections": result_sections,
        "stats": {
            "events_processed": len(existing_card_ids) + len(new_cards),
            "cards_acted_on": cards_acted_count,
            "compression_ratio": round(compression, 2),
        },
    }

    # 9. Save new snapshot — re-read the current max version to avoid
    #    collision with any incremental snapshots that were written before
    #    the gate closed (the gate only blocks future queue polls).
    now = db_now()
    snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
    all_card_ids = list(set(existing_card_ids + [c["card_id"] for c in new_cards]))

    max_ver_rows = await db.execute_fetchall(
        "SELECT COALESCE(MAX(version), 0) AS mv FROM omni_snapshots WHERE space_id = ?",
        (space_id,),
    )
    new_version = max_ver_rows[0]["mv"] + 1

    await db.execute(
        """INSERT INTO omni_snapshots
           (snapshot_id, space_id, version, generated_at, snapshot_type,
            content_json, card_ids, events_processed, created_at,
            is_delta, base_version, change_summary_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            space_id,
            new_version,
            now,
            snapshot_type,
            json.dumps(content),
            json.dumps(all_card_ids),
            len(all_card_ids),
            now,
            0,     # Full base snapshot, not a delta
            None,
            json.dumps(change_summary),
        ),
    )
    await db.commit()

    # Update cache with full state
    _latest_cache[space_id] = {
        "content": content,
        "version": new_version,
        "card_ids": all_card_ids,
        "meta": {
            "snapshot_id": snapshot_id,
            "generated_at": now,
            "snapshot_type": snapshot_type,
        },
    }

    # --- GATE: re-open so queued cards resume on next poll ---
    gate.set()
    log.info("omni_resynthesis_gate_opened", space_id=space_id, reason="resynthesis_complete")

    # 10. Broadcast
    await manager.broadcast({
        "type": "omni_updated",
        "payload": {
            "space_id": space_id,
            "version": new_version,
            "snapshot_type": snapshot_type,
            "sections_count": len(result_sections),
        },
    })

    log.info(
        "omni_resynthesis_complete",
        space_id=space_id,
        version=new_version,
        snapshot_id=snapshot_id,
        items=total_items_after,
        compression=round(compression, 2),
        **{f"changed_{k}": v for k, v in change_summary["counts"].items()},
    )

    return snapshot_id
