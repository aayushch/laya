# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Summary-related tool implementations (daily summary + omni)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from laya.db.sqlite import get_db


async def get_daily_summary(
    summary_date: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Get the daily summary for a given date (defaults to today)."""
    db = await get_db()
    target_date = summary_date or date.today().isoformat()

    if space_id:
        rows = await db.execute_fetchall(
            "SELECT date, space_id, summary_json, card_ids, updated_at "
            "FROM daily_summaries WHERE date = ? AND space_id = ?",
            (target_date, space_id),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT date, space_id, summary_json, card_ids, updated_at "
            "FROM daily_summaries WHERE date = ? ORDER BY space_id",
            (target_date,),
        )

    if not rows:
        return {"date": target_date, "summaries": [], "message": "No summary available for this date."}

    summaries: list[dict[str, Any]] = []
    for r in rows:
        summary = {}
        try:
            summary = json.loads(r["summary_json"])
        except (json.JSONDecodeError, TypeError):
            pass

        card_ids: list[str] = []
        try:
            card_ids = json.loads(r["card_ids"])
        except (json.JSONDecodeError, TypeError):
            pass

        summaries.append({
            "space_id": r["space_id"],
            "summary": summary,
            "card_count": len(card_ids),
            "updated_at": r["updated_at"],
        })

    return {"date": target_date, "summaries": summaries}


async def get_omni_summary(
    space_id: str | None = None,
) -> dict[str, Any]:
    """Get the latest Omni cross-platform rolling summary."""
    db = await get_db()
    target_space = space_id or "default"

    # Fetch the latest full (non-delta) snapshot as the base
    base_rows = await db.execute_fetchall(
        "SELECT snapshot_id, version, content_json, card_ids, "
        "       generated_at, snapshot_type, events_processed "
        "FROM omni_snapshots "
        "WHERE space_id = ? AND is_delta = 0 "
        "ORDER BY version DESC LIMIT 1",
        (target_space,),
    )

    if not base_rows:
        return {"space_id": target_space, "sections": [], "message": "No Omni summary available yet."}

    base = base_rows[0]
    base_version = base["version"]

    try:
        content = json.loads(base["content_json"])
    except (json.JSONDecodeError, TypeError):
        return {"space_id": target_space, "sections": [], "message": "Failed to parse Omni snapshot."}

    # Apply any deltas on top of the base
    delta_rows = await db.execute_fetchall(
        "SELECT content_json FROM omni_snapshots "
        "WHERE space_id = ? AND is_delta = 1 AND base_version = ? "
        "ORDER BY version ASC",
        (target_space, base_version),
    )

    latest_version = base_version
    for dr in delta_rows:
        try:
            delta = json.loads(dr["content_json"])
            # Delta contains replacement items for the "recent" section
            if "sections" in delta:
                for delta_section in delta["sections"]:
                    for i, s in enumerate(content.get("sections", [])):
                        if s.get("type") == delta_section.get("type"):
                            content["sections"][i] = delta_section
                            break
            latest_version += 1
        except (json.JSONDecodeError, TypeError):
            continue

    # Build lean response — sections with items, stats
    sections = content.get("sections", [])
    card_ids: list[str] = []
    try:
        card_ids = json.loads(base["card_ids"])
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "space_id": target_space,
        "version": latest_version,
        "generated_at": base["generated_at"],
        "snapshot_type": base["snapshot_type"],
        "sections": sections,
        "events_processed": base["events_processed"],
        "card_count": len(card_ids),
    }
