"""Generic metadata key-value API.

Stores arbitrary JSON values keyed by a consumer-defined string.
Values are opaque to this API — the format is owned by whoever
reads/writes a given key (n8n workflows, UI settings, etc.).
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Query

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()


@router.get("/metadata")
async def list_metadata(
    prefix: str | None = Query(default=None),
    space_id: str = Query(default="default"),
) -> dict:
    """List metadata entries, optionally filtered by key prefix."""
    db = await get_db()
    if prefix:
        rows = await db.execute_fetchall(
            "SELECT key, value, space_id FROM metadata WHERE space_id = ? AND key LIKE ?",
            (space_id, f"{prefix}%"),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT key, value, space_id FROM metadata WHERE space_id = ?",
            (space_id,),
        )
    return {
        "items": [
            {"key": r["key"], "value": json.loads(r["value"]), "space_id": r["space_id"]}
            for r in rows
        ]
    }


@router.get("/metadata/{key:path}")
async def get_metadata(
    key: str,
    space_id: str = Query(default="default"),
) -> dict:
    """Get a single metadata value by key."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT key, value, space_id FROM metadata WHERE key = ? AND space_id = ?",
        (key, space_id),
    )
    if not rows:
        return {"key": key, "value": None, "space_id": space_id}
    row = rows[0]
    return {"key": row["key"], "value": json.loads(row["value"]), "space_id": row["space_id"]}


@router.put("/metadata/{key:path}")
async def put_metadata(key: str, body: dict) -> dict:
    """Upsert a metadata value. Body: {"value": <any JSON>, "space_id"?: "default"}"""
    space_id = body.get("space_id", "default")
    value = body.get("value")
    if value is None:
        return {"error": "Missing 'value' in request body"}

    serialized = json.dumps(value)
    db = await get_db()
    await db.execute(
        """INSERT INTO metadata (key, value, space_id)
           VALUES (?, ?, ?)
           ON CONFLICT (key, space_id) DO UPDATE SET value = excluded.value""",
        (key, serialized, space_id),
    )
    await db.commit()
    log.info("metadata_upserted", key=key, space_id=space_id)
    return {"key": key, "value": value, "space_id": space_id}


@router.delete("/metadata/{key:path}")
async def delete_metadata(
    key: str,
    space_id: str = Query(default="default"),
) -> dict:
    """Delete a metadata entry."""
    db = await get_db()
    await db.execute(
        "DELETE FROM metadata WHERE key = ? AND space_id = ?",
        (key, space_id),
    )
    await db.commit()
    log.info("metadata_deleted", key=key, space_id=space_id)
    return {"deleted": True, "key": key, "space_id": space_id}
