"""Action execution service — forward approved actions to n8n and record results."""

import json
from datetime import datetime, timezone

import httpx
import structlog

from laya.api.websocket import manager
from laya.config import N8N_URL
from laya.db.sqlite import get_db

log = structlog.get_logger()

# n8n webhook URL mapping: platform -> webhook path
N8N_EXECUTOR_WEBHOOKS: dict[str, str] = {
    "jira": "jira-executor",
    "bitbucket": "bitbucket-executor",
    "slack": "slack-executor",
    "gmail": "gmail-executor",
    "calendar": "calendar-executor",
}


async def execute_action(
    card_id: str,
    action_id: str,
    modifications: dict | None = None,
) -> dict:
    """Execute a suggested action by forwarding it to n8n.

    Flow:
    1. Look up the card + suggested_action from SQLite
    2. Validate card status allows execution
    3. Set card status to 'executing'
    4. Build n8n payload and POST to webhook
    5. Parse response, store in action_log
    6. Update card status to completed/failed
    7. Broadcast card_updated via WebSocket

    Returns:
        Dict with card_id, action_id, status, result_url, error.
    """
    db = await get_db()

    # 1. Look up card and its suggested_actions
    rows = await db.execute_fetchall(
        """SELECT card_id, event_id, suggested_actions, status
           FROM action_cards WHERE card_id = ?""",
        (card_id,),
    )
    if not rows:
        raise ValueError(f"Card not found: {card_id}")

    card_row = rows[0]
    current_status = card_row["status"]

    # 2. Validate status
    if current_status not in ("pending", "approved"):
        raise ValueError(
            f"Card {card_id} status is '{current_status}', "
            "must be 'pending' or 'approved' to execute"
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

    # 4. Update card to 'executing'
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE action_cards SET status = 'executing', updated_at = ? WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "executing"}}
    )

    # 5. Build n8n payload and POST
    target_platform = action["target_platform"]
    webhook_path = N8N_EXECUTOR_WEBHOOKS.get(target_platform, f"{target_platform}-executor")
    webhook_url = f"{N8N_URL}/webhook/{webhook_path}"

    n8n_payload = {
        "action_id": action_id,
        "source_event_id": card_row["event_id"],
        "target": {"platform": target_platform, "connection_id": None},
        "action_type": action["action_type"],
        "payload": payload,
    }

    result_status = "completed"
    result_data: dict = {}
    error_message: str | None = None
    result_url: str | None = None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(webhook_url, json=n8n_payload)
            resp_data = resp.json()

        if resp_data.get("success"):
            result_status = "completed"
            result_data = resp_data.get("result", {})
            result_url = (
                result_data.get("pr_url")
                or result_data.get("url")
                or result_data.get("message_url")
            )
        else:
            result_status = "failed"
            error_message = resp_data.get("error", f"n8n returned status {resp.status_code}")
    except httpx.TimeoutException:
        result_status = "failed"
        error_message = "n8n request timed out"
    except Exception as e:
        result_status = "failed"
        error_message = str(e)

    # 6. Store in action_log
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO action_log
           (action_id, card_id, action_type, target_platform, target_connection_id,
            payload, executed_at, result_status, result_data, error_message, modifications)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            action_id,
            card_id,
            action["action_type"],
            target_platform,
            None,
            json.dumps(payload),
            now,
            result_status,
            json.dumps(result_data) if result_data else None,
            error_message,
            json.dumps(modifications) if modifications else None,
        ),
    )

    # 7. Update card final status
    await db.execute(
        "UPDATE action_cards SET status = ?, updated_at = ? WHERE card_id = ?",
        (result_status, now, card_id),
    )
    await db.commit()

    # 8. Broadcast final status
    broadcast_payload: dict = {"status": result_status}
    if result_url:
        broadcast_payload["result_url"] = result_url
    if error_message:
        broadcast_payload["error"] = error_message

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": broadcast_payload}
    )

    log.info(
        "action_executed",
        card_id=card_id,
        action_id=action_id,
        result_status=result_status,
        result_url=result_url,
    )

    return {
        "card_id": card_id,
        "action_id": action_id,
        "status": result_status,
        "result_url": result_url,
        "error": error_message,
    }
