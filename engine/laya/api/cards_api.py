# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Cards REST API — aggregator.

The 2,957-line monolith was split into cohesive route modules (cards_feed,
cards_lifecycle, cards_readstate, cards_payload, cards_agent, cards_groups) plus
cards_common for the shared row→CardResponse mapper (review §5.4 — P7-6). This
module include_router's them (order-preserving) so the app still mounts one
`cards_router`, re-exports the handful of helpers external modules import by name,
and keeps the small daily-summary endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()

# _row_to_card / _safe_privacy_tier moved to cards_common (shared with the split
# modules + the external trace/trace_api importers). Re-exported here so
# `from laya.api.cards_api import _row_to_card` keeps working (review §5.4 — P7-6).
from laya.api.cards_common import _row_to_card, _safe_privacy_tier  # noqa: E402,F401

# Split-out route modules (P7-6). Each router is include_router'd IN PLACE below —
# at the same spot the endpoints used to live — so route-registration order is
# unchanged (keeps static paths like /cards/grouped matching before /cards/{card_id}).
from laya.api.cards_agent import router as _agent_router  # noqa: E402
from laya.api.cards_feed import router as _feed_router  # noqa: E402
from laya.api.cards_groups import router as _groups_router  # noqa: E402
from laya.api.cards_lifecycle import router as _lifecycle_router  # noqa: E402
from laya.api.cards_payload import router as _payload_router  # noqa: E402
from laya.api.cards_readstate import router as _readstate_router  # noqa: E402

# Re-export helpers that moved into split modules but are imported by name
# elsewhere (scheduler, main startup, processing_rules, tests), so those import
# sites are unchanged.
from laya.api.cards_agent import _stream_entity_agent  # noqa: E402,F401
from laya.api.cards_feed import get_grouped_cards  # noqa: E402,F401
from laya.api.cards_lifecycle import (  # noqa: E402,F401
    _delete_card_cascade,
    clear_stale_polishing_flags,
)


# Feed first: it owns the static /cards + /cards/grouped, which must register
# before /cards/{card_id} (in the lifecycle module) so path matching is correct.
router.include_router(_feed_router)  # flat list + grouped feed (P7-6)
router.include_router(_lifecycle_router)  # dismiss/archive/reopen/detail/done/delete (P7-6)
router.include_router(_readstate_router)  # bookmark / read-state endpoints (P7-6)
router.include_router(_payload_router)  # dismiss + action-payload/polish + classification (P7-6)


@router.get("/summary")
async def get_daily_summary(
    date: str | None = None,
    tz: str | None = None,
    space_id: str | None = None,
) -> dict:
    """Get daily summaries for a given date, grouped by space.

    When ``tz`` is provided the caller's local date is used for the DB
    lookup (summaries are stored under UTC dates).  Optionally filter to
    a single space via ``space_id``.
    """
    if not date:
        if tz:
            try:
                from zoneinfo import ZoneInfo
                local_now = datetime.now(ZoneInfo(tz))
                date = local_now.strftime("%Y-%m-%d")
            except (KeyError, ValueError):
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = await get_db()

    if space_id:
        rows = await db.execute_fetchall(
            """SELECT ds.space_id, ds.summary_json, ds.card_ids, ds.updated_at,
                      s.name AS space_name, s.color AS space_color
               FROM daily_summaries ds
               LEFT JOIN spaces s ON ds.space_id = s.space_id
               WHERE ds.date = ? AND ds.space_id = ?""",
            (date, space_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT ds.space_id, ds.summary_json, ds.card_ids, ds.updated_at,
                      s.name AS space_name, s.color AS space_color
               FROM daily_summaries ds
               LEFT JOIN spaces s ON ds.space_id = s.space_id
               WHERE ds.date = ?""",
            (date,),
        )

    space_summaries = []
    for row in rows:
        try:
            summary = json.loads(row["summary_json"])
            card_ids = json.loads(row["card_ids"])
        except json.JSONDecodeError:
            summary = None
            card_ids = []
        space_summaries.append({
            "space_id": row["space_id"] or "default",
            "space_name": row["space_name"] or "Default",
            "space_color": row["space_color"] or "#F97316",
            "summary": summary,
            "card_ids": card_ids,
            "updated_at": row["updated_at"],
        })

    return {
        "date": date,
        "space_summaries": space_summaries,
    }


router.include_router(_agent_router)  # run-agent / entity-agent / file uploads + streams (P7-6)
router.include_router(_groups_router)  # context-group merge/unlink/related + group summaries (P7-6)


