"""OMNI pipeline — maintain a rolling cross-platform summary.

Incremental updates append to the "recent" section without LLM calls.
Scheduled resynthesis uses the LLM to compress layers progressively.

Delta storage: incremental snapshots store only the diff (added/fused items
+ new card_ids). Resynthesis snapshots store the full structure and serve as
base checkpoints. Reconstruction chains deltas from the nearest base.
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
from laya.llm.client import llm_call
from laya.llm.prompts.omni import (
    build_omni_resynthesis_messages,
    get_omni_json_schema,
)
from laya.models.omni import OmniItem, OmniSection, OmniSnapshot, OmniStats

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# In-memory cache for the latest reconstructed snapshot per space.
# Populated on every write, avoids delta chain reconstruction on hot reads.
# ---------------------------------------------------------------------------
_latest_cache: dict[str, dict] = {}


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
        return c["content"], c["version"], c["card_ids"], c.get("meta", {})

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
# Debounce state for incremental updates (same pattern as summarizer)
# ---------------------------------------------------------------------------
_debounce_lock = asyncio.Lock()
_pending_cards: list[dict] = []
_debounce_task: asyncio.Task | None = None
_DEBOUNCE_SECONDS = 10


async def trigger_omni_update(
    card_id: str,
    card_header: str,
    card_summary: str,
    card_priority: str,
    source_platform: str | None = None,
    space_id: str | None = None,
    entity_id: str | None = None,
) -> None:
    """Queue a new card for Omni incorporation (debounced).

    This does NOT call the LLM — it appends a structured item to the
    "recent" section of the latest snapshot. The LLM is only used during
    scheduled resynthesis to compress layers.
    """
    global _debounce_task

    settings = load_settings()
    if not settings.get("omni", {}).get("enabled", True):
        return

    card_data = {
        "card_id": card_id,
        "card_header": card_header,
        "card_summary": card_summary,
        "card_priority": card_priority,
        "source_platform": source_platform or "unknown",
        "space_id": space_id or "default",
        "entity_id": entity_id,
    }

    async with _debounce_lock:
        _pending_cards.append(card_data)
        if _debounce_task and not _debounce_task.done():
            _debounce_task.cancel()
        from laya.tasks import create_task as create_tracked_task
        _debounce_task = create_tracked_task(_debounced_run())


async def _debounced_run() -> None:
    """Wait for the debounce period, then batch-append all pending cards."""
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # Another update came in, timer reset

    async with _debounce_lock:
        cards = list(_pending_cards)
        _pending_cards.clear()

    if not cards:
        return

    try:
        await _append_to_recent(cards)
    except Exception as e:
        log.error("omni_incremental_update_failed", error=str(e))


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
        now = datetime.now(timezone.utc).isoformat()

        # Create a new incremental snapshot (version++)
        new_snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
        new_version = version + 1

        if is_first:
            # First snapshot ever — store as full base (no delta possible)
            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed, created_at,
                    is_delta, base_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_snapshot_id, space_id, new_version, now, "incremental",
                    json.dumps(content), json.dumps(all_card_ids),
                    len(all_card_ids), now, 0, None,
                ),
            )
        else:
            # Compute delta from old state and store only the diff
            delta = _compute_delta(old_recent_items, recent_section.get("items", []))
            base_ver = await _find_base_version(db, space_id, version)

            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed, created_at,
                    is_delta, base_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_snapshot_id, space_id, new_version, now, "incremental",
                    json.dumps(delta), json.dumps(new_card_ids),
                    len(all_card_ids), now, 1, base_ver,
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
            snapshot_id = await _resynthesize_space(db, sid, density, snapshot_type)
            if snapshot_id:
                created_ids.append(snapshot_id)
        except Exception as e:
            log.error("omni_resynthesis_failed", space_id=sid, error=str(e))

    return created_ids


async def _resynthesize_space(db, space_id: str, density: str, snapshot_type: str = "scheduled") -> str | None:
    """Resynthesize Omni for a single space."""

    # 1. Load latest snapshot (reconstructed if delta chain)
    current_snapshot, current_version, existing_card_ids, _meta = await _load_full_snapshot(db, space_id)

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

    # 3. Query recent cards (since last resynthesis of any type)
    last_synth_row = await db.execute_fetchall(
        """SELECT generated_at FROM omni_snapshots
           WHERE space_id = ? AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )
    since = last_synth_row[0]["generated_at"] if last_synth_row else "2000-01-01T00:00:00"

    card_rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.header, ac.summary, ac.priority, ac.persona,
                  ac.status, ac.user_feedback, ac.category,
                  e.source_platform, e.actor_name
           FROM action_cards ac
           LEFT JOIN events e ON ac.event_id = e.event_id
           WHERE ac.space_id = ? AND ac.created_at > ?
           ORDER BY ac.created_at DESC
           LIMIT 100""",
        (space_id, since),
    )

    new_cards = [
        {
            "card_id": row["card_id"],
            "header": row["header"],
            "summary": row["summary"],
            "priority": row["priority"],
            "source_platform": row["source_platform"] or "unknown",
            "user_feedback": row["user_feedback"],
            "status": row["status"],
        }
        for row in card_rows
    ]

    # 4. Separate user-acted cards (for higher weight in prompt)
    acted_cards = [
        c for c in new_cards
        if c.get("user_feedback") or c.get("status") in ("done", "dismissed", "archived")
    ]

    # If no snapshot and no new cards, nothing to do
    if not current_snapshot and not new_cards:
        log.info("omni_resynthesis_skipped_empty", space_id=space_id)
        return None

    # 5. Build LLM prompt
    messages = build_omni_resynthesis_messages(
        current_snapshot=current_snapshot,
        new_cards=new_cards,
        acted_cards=acted_cards,
        pinned_items=pinned_items,
        density=density,
        space_id=space_id,
    )

    schema = get_omni_json_schema()

    # 6. Call LLM
    try:
        response = await llm_call(
            role="omni",
            messages=messages,
            response_schema=schema,
            step="omni_resynthesis",
            temperature=0.3,
            max_tokens=4000,
            space_id=space_id,
        )

        if not response.parsed:
            truncation_hint = " (response was truncated)" if response.truncated else ""
            raise ValueError(
                f"LLM returned malformed JSON for Omni resynthesis{truncation_hint} "
                f"(output_tokens={response.output_tokens}, model={response.model})"
            )

        result_sections = response.parsed.get("sections", [])

    except Exception as e:
        log.error("omni_resynthesis_llm_failed", space_id=space_id, error=str(e))
        # On LLM failure, keep the current snapshot unchanged
        return None

    # 7. Inject space_id into all items
    for section in result_sections:
        for item in section.get("items", []):
            item["space_id"] = space_id

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

    # 9. Save new snapshot
    now = datetime.now(timezone.utc).isoformat()
    new_version = current_version + 1
    snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
    all_card_ids = list(set(existing_card_ids + [c["card_id"] for c in new_cards]))

    await db.execute(
        """INSERT INTO omni_snapshots
           (snapshot_id, space_id, version, generated_at, snapshot_type,
            content_json, card_ids, events_processed, created_at,
            is_delta, base_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    )

    return snapshot_id
