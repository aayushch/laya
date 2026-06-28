# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Centralized card status lifecycle — single source of truth for transitions."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.db.sqlite import get_db

log = structlog.get_logger()

VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending":           {"ready", "dismissed", "archived", "done", "agent_running", "executing", "failed"},
    "ready":             {"requires_approval", "dismissed", "archived", "done", "agent_running", "executing", "failed"},
    "requires_approval": {"ready", "dismissed", "archived", "done", "executing"},
    "agent_running":     {"ready", "done", "failed", "dismissed", "awaiting_input", "executing"},
    "awaiting_input":    {"ready", "done", "dismissed", "agent_running"},
    "executing":         {"done", "failed"},
    "done":              {"archived", "agent_running"},  # agent_running: user resumed a completed workspace session with a new prompt
    "failed":            {"ready", "dismissed", "archived", "executing", "agent_running"},
    "dismissed":         {"ready", "archived"},
    "archived":          set(),
}

TERMINAL_STATUSES = {"done", "dismissed", "archived"}

# "No longer an actionable card." Distinct from TERMINAL_STATUSES on purpose:
# `failed` is excluded from TERMINAL_STATUSES (a failed card sets no resolved_at
# and can be retried — see the resolved_at handling below) but IS inactive for
# the purposes of grouping / sibling auto-resolution / feed filters. Use this set
# (and is_active) instead of re-spelling the status list inline in SQL.
INACTIVE_STATUSES = {"done", "dismissed", "failed", "archived"}


def is_active(status: str) -> bool:
    """True when a card is still actionable (not done/dismissed/failed/archived)."""
    return status not in INACTIVE_STATUSES


async def transition_card_status(
    card_id: str,
    new_status: str,
    *,
    actor: str,
    reason: str | None = None,
    feedback_type: str | None = None,
    failed_stage: str | None = None,
    last_error: str | None = None,
    save_previous: bool = True,
    extra_fields: dict | None = None,
) -> str:
    """Validate and apply a status transition on an action card.

    Args:
        card_id: The card to transition.
        new_status: Target status.
        actor: Who is making the change ("user", "processing_rule", "agent", "pipeline", "executor").
        reason: Dismissal reason (for user_feedback column).
        feedback_type: Feedback type (for dismiss with classification feedback).
        failed_stage: Which pipeline stage failed (for failed status).
        last_error: Error message (for failed status).
        save_previous: Whether to save current status as previous_status.
        extra_fields: Additional columns to set (e.g. {"selected_action_id": "act_123"}).

    Returns:
        The previous status.

    Raises:
        ValueError: If the card doesn't exist or the transition is invalid.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,),
    )
    if not rows:
        raise ValueError(f"Card {card_id} not found")

    current = rows[0]["status"]
    allowed = VALID_STATUS_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(f"Invalid transition {current} -> {new_status} for card {card_id}")

    now = datetime.now(timezone.utc).isoformat()

    sets = ["status = ?", "updated_at = ?"]
    params: list = [new_status, now]

    if save_previous and current != new_status:
        sets.append("previous_status = ?")
        params.append(current)

    if new_status in TERMINAL_STATUSES:
        sets.append("resolved_at = ?")
        params.append(now)

    if new_status == "dismissed" and reason is not None:
        sets.append("user_feedback = ?")
        params.append(reason)

    if feedback_type is not None:
        sets.append("feedback_type = ?")
        params.append(feedback_type)

    if new_status == "failed":
        if failed_stage is not None:
            sets.append("failed_stage = ?")
            params.append(failed_stage)
        if last_error is not None:
            sets.append("last_error = ?")
            params.append(last_error)

    if new_status not in TERMINAL_STATUSES and new_status != "failed":
        sets.append("resolved_at = NULL")
        sets.append("failed_stage = NULL")
        sets.append("last_error = NULL")

    if extra_fields:
        for col, val in extra_fields.items():
            sets.append(f"{col} = ?")
            params.append(val)

    params.append(card_id)
    await db.execute(
        f"UPDATE action_cards SET {', '.join(sets)} WHERE card_id = ?",
        params,
    )
    await db.commit()

    broadcast_payload: dict = {"status": new_status}
    if extra_fields:
        broadcast_payload.update(extra_fields)
    await manager.broadcast({"type": "card_updated", "card_id": card_id, "payload": broadcast_payload})

    log.info("card_status_transition", card_id=card_id, from_status=current, to_status=new_status, actor=actor)
    return current
