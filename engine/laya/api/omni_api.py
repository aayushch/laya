"""Omni API — rolling cross-platform summary endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class PinRequest(BaseModel):
    space_id: str = "default"
    text: str
    source_cards: list[str] = []
    platforms: list[str] = []


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@router.get("/omni")
async def get_omni(space_id: str = "default", version: int | None = None):
    """Get the latest (or specific version) Omni snapshot."""
    db = await get_db()

    if version is not None:
        rows = await db.execute_fetchall(
            """SELECT snapshot_id, space_id, version, generated_at, snapshot_type,
                      content_json, card_ids, events_processed, created_at
               FROM omni_snapshots
               WHERE space_id = ? AND version = ?""",
            (space_id, version),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT snapshot_id, space_id, version, generated_at, snapshot_type,
                      content_json, card_ids, events_processed, created_at
               FROM omni_snapshots
               WHERE space_id = ?
               ORDER BY version DESC LIMIT 1""",
            (space_id,),
        )

    if not rows:
        # Return empty snapshot rather than 404 — the UI needs something to render
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

    row = rows[0]
    content = json.loads(row["content_json"])

    return {
        "snapshot_id": row["snapshot_id"],
        "space_id": row["space_id"],
        "version": row["version"],
        "generated_at": row["generated_at"],
        "snapshot_type": row["snapshot_type"],
        "sections": content.get("sections", []),
        "stats": content.get("stats", {}),
        "card_ids": json.loads(row["card_ids"]),
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
# Resynthesis
# ---------------------------------------------------------------------------


@router.post("/omni/resynthesis")
async def trigger_resynthesis(space_id: str = "default"):
    """Manually trigger a full Omni resynthesis."""
    from laya.pipeline.omni import run_omni_resynthesis

    try:
        snapshot_ids = await run_omni_resynthesis(space_id=space_id)
        return {
            "status": "ok",
            "snapshot_ids": snapshot_ids,
            "space_id": space_id,
        }
    except Exception as e:
        log.error("omni_resynthesis_api_failed", space_id=space_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


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
