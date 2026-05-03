"""Action execution service — delegates to egress module and manages card lifecycle."""

import json
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.egress import execute as egress_execute
from laya.egress.models import EgressRequest
from laya.llm.client import log_to_audit

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

    # 2. Validate status — allow execution from agent_running too, so users
    # can invoke actions while an agent session is active on the card.
    if current_status not in ("pending", "ready", "failed", "agent_running"):
        raise ValueError(
            f"Card {card_id} status is '{current_status}', "
            "must be 'pending', 'ready', 'failed', or 'agent_running' to execute"
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
    from laya.models.card_lifecycle import transition_card_status
    await transition_card_status(
        card_id, "executing", actor="executor",
        extra_fields={"selected_action_id": action_id},
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

    # 6. Resolve connection_id from the originating event so the egress
    # module picks the correct executor workflow when multiple connections
    # exist for the same platform (e.g. two Jira instances).
    # The event stores the n8n workflow_id as source_connection_id, so we
    # look up the corresponding egress connection_id from the sources table.
    connection_id = None
    if card_row["event_id"]:
        evt_rows = await db.execute_fetchall(
            "SELECT source_connection_id FROM events WHERE event_id = ?",
            (card_row["event_id"],),
        )
        if evt_rows and evt_rows[0]["source_connection_id"]:
            workflow_id = evt_rows[0]["source_connection_id"]
            conn_rows = await db.execute_fetchall(
                "SELECT connection_id FROM sources WHERE workflow_id = ? AND connection_id IS NOT NULL LIMIT 1",
                (workflow_id,),
            )
            if conn_rows:
                connection_id = conn_rows[0]["connection_id"]

    # 7. Delegate to egress module
    request = EgressRequest(
        platform=target_platform,
        action_type=action_type,
        payload=payload,
        connection_id=connection_id,
        source_card_id=card_id,
        source_event_id=card_row["event_id"],
        space_id=card_row["space_id"],
    )

    result = await egress_execute(request)

    # 8. Store in action_log
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
                connection_id,
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

    # 9. Update card final status
    try:
        await transition_card_status(
            card_id, result_status, actor="executor",
            failed_stage="action_execution" if result_status == "failed" else None,
            last_error=result.error if result_status == "failed" else None,
        )
    except (ValueError, Exception) as e:
        log.error("card_status_update_failed", card_id=card_id, error=str(e))

    log.info(
        "action_executed",
        card_id=card_id,
        action_id=action_id,
        result_status=result_status,
        result_url=result.result_url,
        error=result.error,
    )

    await log_to_audit(
        event_id=card_row["event_id"], card_id=card_id, step="execute",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=result.success,
        error=result.error,
        metadata={"action_id": action_id, "action_type": action["action_type"],
                  "target_platform": target_platform,
                  "result_url": result.result_url},
    )

    return {
        "card_id": card_id,
        "action_id": action_id,
        "status": result_status,
        "result_url": result.result_url,
        "error": result.error,
    }
