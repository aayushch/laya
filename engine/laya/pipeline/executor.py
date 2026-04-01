"""Action execution service — delegates to egress module and manages card lifecycle."""

import json
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.egress import execute as egress_execute
from laya.egress.models import EgressRequest

log = structlog.get_logger()


async def execute_action(
    card_id: str,
    action_id: str,
    modifications: dict | None = None,
) -> dict:
    """Execute a suggested action by delegating to the egress module.

    This function manages the card lifecycle (status, action_log, WebSocket).
    The actual platform interaction is handled entirely by the egress module.

    Flow:
    1. Look up the card + suggested_action from SQLite
    2. Validate card status allows execution
    3. Set card status to 'executing'
    4. Build EgressRequest and delegate to egress.execute()
    5. Store result in action_log
    6. Update card status to done/failed
    7. Broadcast card_updated via WebSocket

    Returns:
        Dict with card_id, action_id, status, result_url, error.
    """
    db = await get_db()

    # 1. Look up card and its suggested_actions
    rows = await db.execute_fetchall(
        """SELECT card_id, event_id, suggested_actions, status, space_id
           FROM action_cards WHERE card_id = ?""",
        (card_id,),
    )
    if not rows:
        raise ValueError(f"Card not found: {card_id}")

    card_row = rows[0]
    current_status = card_row["status"]

    # 2. Validate status
    if current_status not in ("pending", "ready", "requires_approval", "failed"):
        raise ValueError(
            f"Card {card_id} status is '{current_status}', "
            "must be 'pending', 'ready', 'requires_approval', or 'failed' to execute"
        )

    # 3. Find the specific action from suggested_actions JSON
    suggested_actions = json.loads(card_row["suggested_actions"] or "[]")
    action = None
    for a in suggested_actions:
        if a["action_id"] == action_id:
            action = a
            break

    if action is None:
        raise ValueError(f"Action {action_id} not found on card {card_id}")

    # Apply user modifications to payload
    payload = action.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"raw": payload}
    if modifications:
        payload.update(modifications)

    # 4. Update card to 'executing' and record selected action
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET status = 'executing', selected_action_id = ?, updated_at = ? WHERE card_id = ?",
        (action_id, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "executing", "selected_action_id": action_id}}
    )

    # 5. Resolve target platform (handle calendar detection)
    target_platform = action["target_platform"]
    action_type = action["action_type"]
    if target_platform == "gmail" and (
        payload.get("action") == "create_calendar_event"
        or action_type == "calendar"
    ):
        target_platform = "google_calendar"

    # Remap LLM mistakes: send_message is Slack-only, Gmail uses send_email
    if target_platform in ("gmail", "outlook") and action_type == "send_message":
        log.warning("action_type_remapped", original="send_message", corrected="send_email", platform=target_platform)
        action_type = "send_email"

    # 6. Delegate to egress module
    request = EgressRequest(
        platform=target_platform,
        action_type=action_type,
        payload=payload,
        source_card_id=card_id,
        source_event_id=card_row["event_id"],
        space_id=card_row["space_id"],
    )

    result = await egress_execute(request)

    # 7. Store in action_log
    result_status = "done" if result.success else "failed"
    now = datetime.now(timezone.utc).isoformat()

    try:
        await db.execute(
            """INSERT INTO action_log
               (action_id, card_id, action_type, target_platform, target_connection_id,
                payload, executed_at, result_status, result_data, error_message, modifications, retryable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(action_id) DO UPDATE SET
                executed_at = excluded.executed_at,
                result_status = excluded.result_status,
                result_data = excluded.result_data,
                error_message = excluded.error_message,
                retryable = excluded.retryable""",
            (
                action_id,
                card_id,
                action["action_type"],
                target_platform,
                None,
                json.dumps(payload),
                now,
                result_status,
                json.dumps(result.result_data) if result.result_data else None,
                result.error,
                json.dumps(modifications) if modifications else None,
                result.retryable,
            ),
        )
    except Exception as db_err:
        log.error("action_log_write_failed", card_id=card_id, error=str(db_err))

    # 8. Update card final status
    try:
        if result_status == "failed":
            await db.execute(
                "UPDATE action_cards SET status = ?, failed_stage = 'action_execution', updated_at = ? WHERE card_id = ?",
                (result_status, now, card_id),
            )
        else:
            await db.execute(
                "UPDATE action_cards SET status = ?, failed_stage = NULL, updated_at = ? WHERE card_id = ?",
                (result_status, now, card_id),
            )
        await db.commit()
    except Exception as db_err:
        log.error("card_status_update_failed", card_id=card_id, error=str(db_err))

    # 9. Broadcast final status
    broadcast_payload: dict = {"status": result_status}
    if result.result_url:
        broadcast_payload["result_url"] = result.result_url
    if result.error:
        broadcast_payload["error"] = result.error

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": broadcast_payload}
    )

    log.info(
        "action_executed",
        card_id=card_id,
        action_id=action_id,
        result_status=result_status,
        result_url=result.result_url,
        error=result.error,
    )

    return {
        "card_id": card_id,
        "action_id": action_id,
        "status": result_status,
        "result_url": result.result_url,
        "error": result.error,
    }
