"""Omni API — rolling cross-platform summary endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()

# Track in-flight resynthesis per space to prevent concurrent runs
_resynthesis_in_progress: set[str] = set()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class PinRequest(BaseModel):
    space_id: str = "default"
    text: str
    source_cards: list[str] = []
    platforms: list[str] = []


class BookmarkRequest(BaseModel):
    space_id: str = "default"
    source_card_id: str  # first source_card ID — unique identifier for the item
    bookmarked: bool = True


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@router.get("/omni")
async def get_omni(space_id: str = "default", version: int | None = None):
    """Get the latest (or specific version) Omni snapshot.

    Handles delta reconstruction transparently — callers always receive
    the full snapshot regardless of how it's stored.
    """
    from laya.pipeline.omni import _load_full_snapshot

    db = await get_db()

    # Reconstruct full state (handles delta chains automatically)
    content, ver, card_ids, meta = await _load_full_snapshot(db, space_id, version)

    if content is None:
        return {
            "snapshot_id": None,
            "space_id": space_id,
            "version": 0,
            "generated_at": None,
            "snapshot_type": None,
            "sections": [],
            "stats": {"events_processed": 0, "cards_acted_on": 0, "compression_ratio": 0.0},
            "card_ids": [],
        }

    return {
        "snapshot_id": meta.get("snapshot_id"),
        "space_id": space_id,
        "version": ver,
        "generated_at": meta.get("generated_at"),
        "snapshot_type": meta.get("snapshot_type"),
        "sections": content.get("sections", []),
        "stats": content.get("stats", {}),
        "card_ids": card_ids,
    }


@router.get("/omni/history")
async def get_omni_history(space_id: str = "default", limit: int = 30):
    """List snapshot versions for time-slider navigation."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ?
           ORDER BY version DESC
           LIMIT ?""",
        (space_id, limit),
    )

    return {
        "space_id": space_id,
        "snapshots": [
            {
                "snapshot_id": row["snapshot_id"],
                "version": row["version"],
                "generated_at": row["generated_at"],
                "snapshot_type": row["snapshot_type"],
                "events_processed": row["events_processed"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Timeline (logarithmic sampling across retention window)
# ---------------------------------------------------------------------------


@router.get("/omni/timeline")
async def get_omni_timeline(space_id: str = "default"):
    """Return a three-tier sampled timeline for the Omni time-travel UI.

    Tiers:
      - today (past 24h): every snapshot
      - this_week (1-7 days ago): latest snapshot per hour
      - earlier (7+ days ago): only synthesis snapshots
    """
    db = await get_db()
    now = datetime.now(timezone.utc)
    today_start = (now - timedelta(hours=24)).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()
    now_iso = now.isoformat()

    def _row_to_entry(row):
        return {
            "snapshot_id": row["snapshot_id"],
            "version": row["version"],
            "generated_at": row["generated_at"],
            "snapshot_type": row["snapshot_type"],
            "events_processed": row["events_processed"],
        }

    # Tier 1: Today — all snapshots
    today_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ? AND generated_at >= ?
           ORDER BY generated_at ASC""",
        (space_id, today_start),
    )

    # Tier 2: This week — latest snapshot per hour bucket
    week_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE snapshot_id IN (
               SELECT snapshot_id FROM (
                   SELECT snapshot_id,
                          ROW_NUMBER() OVER (
                              PARTITION BY strftime('%Y-%m-%d %H', generated_at)
                              ORDER BY version DESC
                          ) AS rn
                   FROM omni_snapshots
                   WHERE space_id = ? AND generated_at >= ? AND generated_at < ?
               ) WHERE rn = 1
           )
           ORDER BY generated_at ASC""",
        (space_id, week_start, today_start),
    )

    # Tier 3: Earlier — only synthesis snapshots
    earlier_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ? AND generated_at < ?
             AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY generated_at ASC""",
        (space_id, week_start),
    )

    return {
        "space_id": space_id,
        "segments": [
            {
                "tier": "earlier",
                "label": "Earlier",
                "range_start": None,
                "range_end": week_start,
                "entries": [_row_to_entry(r) for r in earlier_rows],
            },
            {
                "tier": "this_week",
                "label": "This Week",
                "range_start": week_start,
                "range_end": today_start,
                "entries": [_row_to_entry(r) for r in week_rows],
            },
            {
                "tier": "today",
                "label": "Recent",
                "range_start": today_start,
                "range_end": now_iso,
                "entries": [_row_to_entry(r) for r in today_rows],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Resynthesis
# ---------------------------------------------------------------------------


@router.post("/omni/resynthesis")
async def trigger_resynthesis(space_id: str = "default"):
    """Manually trigger a full Omni resynthesis (runs in background).

    Returns immediately with 202 Accepted. The client should listen for
    the ``omni_updated`` WebSocket event to know when resynthesis is done.
    """
    import asyncio
    from laya.pipeline.omni import run_omni_resynthesis

    if space_id in _resynthesis_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Resynthesis already in progress for this space",
        )

    _resynthesis_in_progress.add(space_id)

    async def _run():
        try:
            await run_omni_resynthesis(space_id=space_id, snapshot_type="manual")
        except Exception as e:
            log.error("omni_resynthesis_api_failed", space_id=space_id, error=str(e))
        finally:
            _resynthesis_in_progress.discard(space_id)

    asyncio.create_task(_run())

    from starlette.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "space_id": space_id},
    )


# ---------------------------------------------------------------------------
# Pin endpoints
# ---------------------------------------------------------------------------


@router.get("/omni/pins")
async def list_pins(space_id: str = "default"):
    """List all pinned items for a space."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT pin_id, space_id, item_text, source_card_ids, platforms, pinned_at
           FROM omni_pins
           WHERE space_id = ?
           ORDER BY pinned_at DESC""",
        (space_id,),
    )

    return {
        "space_id": space_id,
        "pins": [
            {
                "pin_id": row["pin_id"],
                "space_id": row["space_id"],
                "item_text": row["item_text"],
                "source_card_ids": json.loads(row["source_card_ids"]),
                "platforms": json.loads(row["platforms"]),
                "pinned_at": row["pinned_at"],
            }
            for row in rows
        ],
    }


@router.post("/omni/pin")
async def pin_item(req: PinRequest):
    """Pin an item to survive compression."""
    db = await get_db()

    pin_id = f"pin_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO omni_pins
           (pin_id, space_id, item_text, source_card_ids, platforms, pinned_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            pin_id,
            req.space_id,
            req.text,
            json.dumps(req.source_cards),
            json.dumps(req.platforms),
            now,
        ),
    )
    await db.commit()

    log.info("omni_item_pinned", pin_id=pin_id, space_id=req.space_id)

    return {
        "pin_id": pin_id,
        "space_id": req.space_id,
        "item_text": req.text,
        "source_card_ids": req.source_cards,
        "platforms": req.platforms,
        "pinned_at": now,
    }


@router.delete("/omni/pin/{pin_id}")
async def unpin_item(pin_id: str):
    """Remove a pin."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT pin_id FROM omni_pins WHERE pin_id = ?", (pin_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Pin not found")

    await db.execute("DELETE FROM omni_pins WHERE pin_id = ?", (pin_id,))
    await db.commit()

    log.info("omni_item_unpinned", pin_id=pin_id)

    return {"status": "ok", "pin_id": pin_id}


# ---------------------------------------------------------------------------
# Bookmark endpoints
# ---------------------------------------------------------------------------


@router.post("/omni/bookmark")
async def toggle_bookmark(req: BookmarkRequest):
    """Toggle bookmark on an item in the latest snapshot.

    Bookmarks live inside the snapshot JSON — they die when the item is
    distilled away during resynthesis. Handles both full and delta rows.
    """
    from laya.pipeline.omni import _latest_cache

    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT snapshot_id, content_json, is_delta
           FROM omni_snapshots
           WHERE space_id = ?
           ORDER BY version DESC
           LIMIT 1""",
        (req.space_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No snapshot found")

    snapshot_id = rows[0]["snapshot_id"]
    is_delta = rows[0]["is_delta"]

    if is_delta:
        # Delta row — store bookmark override in the delta JSON
        delta = json.loads(rows[0]["content_json"])
        delta.setdefault("bookmark_overrides", {})[req.source_card_id] = req.bookmarked
        await db.execute(
            "UPDATE omni_snapshots SET content_json = ? WHERE snapshot_id = ?",
            (json.dumps(delta), snapshot_id),
        )
    else:
        # Full snapshot — walk sections and flip the boolean directly
        content = json.loads(rows[0]["content_json"])
        sections = content.get("sections", [])

        found = False
        for section in sections:
            for item in section.get("items", []):
                cards = item.get("source_cards", [])
                if cards and cards[0] == req.source_card_id:
                    item["bookmarked"] = req.bookmarked
                    found = True
                    break
            if found:
                break

        if not found:
            raise HTTPException(status_code=404, detail="Item not found in snapshot")

        content["sections"] = sections
        await db.execute(
            "UPDATE omni_snapshots SET content_json = ? WHERE snapshot_id = ?",
            (json.dumps(content), snapshot_id),
        )

    await db.commit()

    # Invalidate cache so next read reconstructs with the bookmark change
    _latest_cache.pop(req.space_id, None)

    log.info(
        "omni_item_bookmark_toggled",
        source_card_id=req.source_card_id,
        bookmarked=req.bookmarked,
        space_id=req.space_id,
    )

    return {"status": "ok", "bookmarked": req.bookmarked}
