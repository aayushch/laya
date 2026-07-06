# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Card dismiss + action-payload edit/polish + classification endpoints (split from cards_api — P7-6)."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import log_to_audit
from laya.pipeline.summarize import trigger_summary_status_update
from laya.tasks import create_task as create_tracked_task

log = structlog.get_logger()
router = APIRouter()


class DismissRequest(BaseModel):
    reason: str | None = None
    feedback_type: str | None = None


@router.post("/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str, body: DismissRequest | None = None) -> dict:
    """Dismiss an action card with optional feedback."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    current = rows[0]["status"]
    reason = body.reason if body else None
    feedback_type = body.feedback_type if body else None

    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(
            card_id, "dismissed", actor="user",
            reason=reason, feedback_type=feedback_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    now = db_now()
    await db.execute(
        "UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "dismiss", "previous_status": current,
                  "feedback_type": feedback_type},
    )

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "dismissed"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "dismissed", "card_id": card_id}


class UpdateActionPayloadRequest(BaseModel):
    action_id: str
    payload: dict[str, Any]


@router.post("/cards/{card_id}/action-payload")
async def update_action_payload(card_id: str, body: UpdateActionPayloadRequest) -> dict:
    """Update a suggested action's payload (e.g. user edits an email draft)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT suggested_actions FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    actions = json.loads(row["suggested_actions"]) if row["suggested_actions"] else []
    found = False
    updated_action: dict | None = None
    for action in actions:
        if action.get("action_id") == body.action_id:
            action["payload"].update(body.payload)
            # Mark that the user has manually edited this draft — this unlocks
            # the Polish action in the UI. Only set when caller did not supply
            # its own value (e.g. the polish task itself preserves the flag).
            if "_edited" not in body.payload:
                action["payload"]["_edited"] = True
            found = True
            updated_action = action
            break

    if not found:
        raise HTTPException(status_code=404, detail="Action not found")

    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    # Broadcast so other clients (and the feed's selectedCard snapshot) refresh
    # the action payload without needing a full card reload.
    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": body.action_id,
            "payload": {"payload": updated_action["payload"] if updated_action else {}},
        }
    )

    return {"status": "updated", "card_id": card_id, "action_id": body.action_id}


_POLISH_EDITABLE_FIELDS = ("body", "comment", "message", "description")


def _strip_fence_wrap(text: str) -> str:
    """Remove wrapping triple-backtick fences the LLM might add despite instructions."""
    if text.startswith("```") and text.endswith("```") and len(text) > 6:
        inner = text[3:-3]
        # Drop an optional language tag on the first line
        newline = inner.find("\n")
        if 0 <= newline <= 20 and inner[:newline].strip().isalnum():
            inner = inner[newline + 1:]
        return inner.strip()
    return text


def _find_editable_field(payload: dict) -> str | None:
    """Pick the main long-form text field in an action payload."""
    for key in _POLISH_EDITABLE_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return key
    return None


class PolishActionPayloadRequest(BaseModel):
    action_id: str


@router.post("/cards/{card_id}/action-payload/polish")
async def polish_action_payload(card_id: str, body: PolishActionPayloadRequest) -> dict:
    """Kick off an async LLM rewrite of a draft action payload.

    Returns immediately after marking the action as polishing. The actual LLM
    call runs as a background task; on completion the polished text is written
    back to the action payload and an `action_payload_updated` WS event fires.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT suggested_actions, space_id FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    actions = json.loads(row["suggested_actions"]) if row["suggested_actions"] else []
    target_action: dict | None = None
    for action in actions:
        if action.get("action_id") == body.action_id:
            target_action = action
            break
    if target_action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    payload = target_action.get("payload") or {}
    if payload.get("_polishing"):
        raise HTTPException(status_code=409, detail="Polish already in progress")

    editable_field = _find_editable_field(payload)
    if not editable_field:
        raise HTTPException(status_code=400, detail="No editable text field in this action")

    draft_text = payload[editable_field]
    if not isinstance(draft_text, str) or not draft_text.strip():
        raise HTTPException(status_code=400, detail="Draft is empty")

    platform = target_action.get("target_platform")
    space_id = row["space_id"] if "space_id" in row.keys() else None

    # Mark as polishing in DB so a re-mounted UI sees the spinner state.
    payload["_polishing"] = True
    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": body.action_id,
            "payload": {"payload": payload},
        }
    )

    create_tracked_task(
        _run_polish(
            card_id=card_id,
            action_id=body.action_id,
            editable_field=editable_field,
            draft_text=draft_text,
            platform=platform,
            space_id=space_id,
        ),
        name=f"polish_{card_id}_{body.action_id}",
    )

    return {"status": "polishing", "card_id": card_id, "action_id": body.action_id}


async def _run_polish(
    *,
    card_id: str,
    action_id: str,
    editable_field: str,
    draft_text: str,
    platform: str | None,
    space_id: str | None,
) -> None:
    """Background task: call the LLM, write polished text back to the action."""
    from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
    from laya.llm.prompts.chat import build_polish_messages

    polish_schema = {
        "name": "polish_output",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "polished": {
                    "type": "string",
                    "description": "The polished/rewritten text.",
                },
            },
            "required": ["polished"],
            "additionalProperties": False,
        },
    }

    polished_text: str | None = None
    error_message: str | None = None
    try:
        response = await llm_call(
            role="chat",
            messages=build_polish_messages(draft_text, platform),
            step="polish_draft",
            temperature=0.4,
            max_tokens=DEFAULT_MAX_TOKENS,
            card_id=card_id,
            space_id=space_id,
            response_schema=polish_schema,
        )
        if response.parsed and isinstance(response.parsed, dict):
            polished_text = response.parsed.get("polished", "")
        else:
            polished_text = _strip_fence_wrap((response.content or "").strip())
        if not polished_text:
            error_message = "Polish returned empty response"
    except Exception as exc:  # noqa: BLE001 — surface any LLM failure to the user
        log.exception("polish_draft_failed", card_id=card_id, action_id=action_id)
        error_message = str(exc) or "Polish failed"

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT suggested_actions FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        return
    actions = json.loads(rows[0]["suggested_actions"]) if rows[0]["suggested_actions"] else []
    updated_payload: dict | None = None
    for action in actions:
        if action.get("action_id") != action_id:
            continue
        payload = action.get("payload") or {}
        payload["_polishing"] = False
        if polished_text and not error_message:
            payload[editable_field] = polished_text
            payload["_polished_at"] = db_now()
            payload.pop("_polish_error", None)
        elif error_message:
            payload["_polish_error"] = error_message
        action["payload"] = payload
        updated_payload = payload
        break

    await db.execute(
        "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
        (json.dumps(actions), card_id),
    )
    await db.commit()

    await manager.broadcast(
        {
            "type": "action_payload_updated",
            "card_id": card_id,
            "action_id": action_id,
            "payload": {"payload": updated_payload or {}},
        }
    )


class UpdateClassificationRequest(BaseModel):
    priority: str | None = None
    persona: str | None = None
    rule_text: str | None = None


@router.patch("/cards/{card_id}/classification")
async def update_classification(card_id: str, body: UpdateClassificationRequest) -> dict:
    """Update a card's priority/persona and log corrections for the learning loop."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.priority, ac.persona, ac.category, ac.summary,
                  ac.space_id, e.source_platform, e.source_raw_event_type
           FROM action_cards ac
           LEFT JOIN events e ON ac.event_id = e.event_id
           WHERE ac.card_id = ?""",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    card = rows[0]
    now = db_now()
    valid_priorities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    valid_personas = {"ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"}

    if body.priority and body.priority not in valid_priorities:
        raise HTTPException(status_code=422, detail=f"Invalid priority: {body.priority}")
    if body.persona and body.persona not in valid_personas:
        raise HTTPException(status_code=422, detail=f"Invalid persona: {body.persona}")

    # Log corrections for changed fields
    corrections = []
    if body.priority and body.priority != card["priority"]:
        corrections.append(("priority", card["priority"], body.priority))
    if body.persona and body.persona != card["persona"]:
        corrections.append(("persona", card["persona"], body.persona))

    if not corrections and not body.rule_text:
        return {"status": "no_changes", "card_id": card_id}

    # Insert correction records
    for field, original, corrected in corrections:
        await db.execute(
            """INSERT INTO classification_corrections
               (card_id, space_id, field, original_value, corrected_value,
                card_summary, category, platform, event_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id, card["space_id"], field, original, corrected,
                card["summary"], card["category"],
                card["source_platform"], card["source_raw_event_type"],
            ),
        )

    # Update the card itself
    update_parts = []
    update_params: list[Any] = []
    if body.priority and body.priority != card["priority"]:
        update_parts.append("priority = ?")
        update_params.append(body.priority)
    if body.persona and body.persona != card["persona"]:
        update_parts.append("persona = ?")
        update_params.append(body.persona)

    if update_parts:
        update_parts.append("updated_at = ?")
        update_params.append(now)
        update_params.append(card_id)
        await db.execute(
            f"UPDATE action_cards SET {', '.join(update_parts)} WHERE card_id = ?",
            tuple(update_params),
        )

    # Create classification rule if provided
    if body.rule_text:
        await db.execute(
            """INSERT INTO classification_rules (space_id, field, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, ?, 'manual', 1, ?, ?)""",
            (card["space_id"], corrections[0][0] if corrections else None, body.rule_text, now, now),
        )

    await db.commit()

    # Broadcast update
    payload: dict[str, Any] = {}
    if body.priority and body.priority != card["priority"]:
        payload["priority"] = body.priority
    if body.persona and body.persona != card["persona"]:
        payload["persona"] = body.persona
    if payload:
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": payload}
        )

    log.info(
        "classification_updated",
        card_id=card_id,
        corrections=[(c[0], c[1], c[2]) for c in corrections],
        rule_added=bool(body.rule_text),
    )
    return {"status": "updated", "card_id": card_id, "corrections": len(corrections)}
