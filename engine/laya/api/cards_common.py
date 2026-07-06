# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the cards_api sub-modules.

`cards_api.py` was split into cohesive route modules (feed, lifecycle,
read-state, payload, agent, context-groups) that all shape SQLite rows into
`CardResponse`. That row→model conversion (and the privacy-tier clamp) lives here
so the modules — and the external `trace`/`trace_api` importers — share one copy
instead of the row-SELECT/mapper drifting per module (review §5.4 — P7-6).
"""

from __future__ import annotations

import json

from laya.models.card import CardResponse, StagedOutput, SuggestedAction


def _safe_privacy_tier(val) -> int:
    try:
        return max(1, min(3, int(val)))
    except (TypeError, ValueError):
        return 2


def _row_to_card(row, slim: bool = False) -> CardResponse:
    """Convert a SQLite Row to a CardResponse, deserializing JSON columns.

    ``slim=True`` omits the two large JSON blobs (``staged_output``,
    ``suggested_actions``) for the grouped-feed *list* payload — they're heavy
    per card, unused by the list/summary UI, and lazily re-fetched by the detail
    panel via ``GET /cards/{id}`` (review §2/§4 — P4-9). ``intelligence`` is kept
    (small, and it's still searched client-side, matching the P4-10 backend scan).
    """
    intelligence = None
    if row["intelligence"]:
        try:
            parsed = json.loads(row["intelligence"])
            intelligence = parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            intelligence = None

    staged_output = None
    if not slim and row["staged_output"]:
        try:
            staged_output = StagedOutput(**json.loads(row["staged_output"]))
        except (json.JSONDecodeError, Exception):
            staged_output = None

    suggested_actions = None
    if not slim and row["suggested_actions"]:
        try:
            raw_actions = json.loads(row["suggested_actions"])
            suggested_actions = [SuggestedAction(**a) for a in raw_actions]
        except (json.JSONDecodeError, Exception):
            suggested_actions = None

    source_context = None
    if "content_metadata" in row.keys() and row["content_metadata"]:
        try:
            meta = json.loads(row["content_metadata"])
            if meta.get("slack_channel_name"):
                source_context = "#" + meta["slack_channel_name"]
        except (json.JSONDecodeError, TypeError):
            pass

    return CardResponse(
        card_id=row["card_id"],
        event_id=row["event_id"],
        created_at=row["created_at"],
        priority=row["priority"],
        persona=row["persona"],
        category=row["category"],
        header=row["header"],
        summary=row["summary"],
        intelligence=intelligence,
        staged_output=staged_output,
        suggested_actions=suggested_actions,
        status=row["status"],
        privacy_tier=_safe_privacy_tier(row["privacy_tier"]),
        has_workspace=bool(row["has_workspace"]),
        resolved_at=row["resolved_at"],
        user_feedback=row["user_feedback"],
        feedback_type=row["feedback_type"],
        confidence=row["confidence"],
        router_model=row["router_model"],
        stager_model=row["stager_model"],
        updated_at=row["updated_at"],
        entity_id=row["entity_id"] if "entity_id" in row.keys() else None,
        source_ref=row["source_ref"] if "source_ref" in row.keys() else None,
        source_url=row["source_url"] if "source_url" in row.keys() else None,
        selected_action_id=row["selected_action_id"] if "selected_action_id" in row.keys() else None,
        actor_name=row["actor_name"] if "actor_name" in row.keys() else None,
        actor_email=row["actor_email"] if "actor_email" in row.keys() else None,
        space_id=row["space_id"] if "space_id" in row.keys() else None,
        space_name=row["space_name"] if "space_name" in row.keys() else None,
        space_color=row["space_color"] if "space_color" in row.keys() else None,
        bookmarked_at=row["bookmarked_at"] if "bookmarked_at" in row.keys() else None,
        read_at=row["read_at"] if "read_at" in row.keys() else None,
        group_active_at=row["group_active_at"] if "group_active_at" in row.keys() else None,
        context_id=row["context_id"] if "context_id" in row.keys() else None,
        last_error=row["last_error"] if "last_error" in row.keys() else None,
        source_context=source_context,
    )
