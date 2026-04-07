"""OMNI pipeline — maintain a rolling cross-platform summary.

Incremental updates append to the "recent" section without LLM calls.
Scheduled resynthesis uses the LLM to compress layers progressively.
"""

from __future__ import annotations

import asyncio
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
    }

    async with _debounce_lock:
        _pending_cards.append(card_data)
        if _debounce_task and not _debounce_task.done():
            _debounce_task.cancel()
        _debounce_task = asyncio.create_task(_debounced_run())


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
        # Load latest snapshot for this space
        rows = await db.execute_fetchall(
            """SELECT snapshot_id, version, content_json, card_ids
               FROM omni_snapshots
               WHERE space_id = ?
               ORDER BY version DESC LIMIT 1""",
            (space_id,),
        )

        if rows:
            snapshot_id = rows[0]["snapshot_id"]
            version = rows[0]["version"]
            content = json.loads(rows[0]["content_json"])
            existing_card_ids = json.loads(rows[0]["card_ids"])
        else:
            # First snapshot for this space — create skeleton
            snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
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

        # Append new cards as items
        new_card_ids = []
        for card in space_cards:
            cid = card["card_id"]
            if cid in existing_card_ids:
                continue

            item = {
                "text": f"{card['card_header']} — {card['card_summary']}"[:200],
                "source_cards": [cid],
                "platforms": [card.get("source_platform", "unknown")],
                "priority": card.get("card_priority", "MEDIUM"),
                "pinned": False,
            }
            recent_section["items"].append(item)
            new_card_ids.append(cid)

        if not new_card_ids:
            continue

        all_card_ids = existing_card_ids + new_card_ids
        now = datetime.now(timezone.utc).isoformat()

        # Create a new incremental snapshot (version++)
        new_snapshot_id = f"omni_{uuid.uuid4().hex[:12]}"
        new_version = version + 1

        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_snapshot_id,
                space_id,
                new_version,
                now,
                "incremental",
                json.dumps(content),
                json.dumps(all_card_ids),
                len(all_card_ids),
                now,
            ),
        )
        await db.commit()

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

    # 1. Load latest snapshot
    rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, content_json, card_ids
           FROM omni_snapshots
           WHERE space_id = ?
           ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )

    current_snapshot = None
    current_version = 0
    existing_card_ids: list[str] = []

    if rows:
        current_version = rows[0]["version"]
        try:
            current_snapshot = json.loads(rows[0]["content_json"])
            existing_card_ids = json.loads(rows[0]["card_ids"])
        except json.JSONDecodeError:
            log.warning("omni_snapshot_corrupted", space_id=space_id)

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
            role="stager",
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
            content_json, card_ids, events_processed, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        ),
    )
    await db.commit()

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
