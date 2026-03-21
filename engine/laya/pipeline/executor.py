"""Action execution service — forward approved actions to n8n and record results."""

import json
from datetime import datetime, timezone

import httpx
import structlog
import tenacity

from laya.api.websocket import manager
from laya.config import get_n8n_config
from laya.db.sqlite import get_db
from laya.http_client import get_client

log = structlog.get_logger()


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

    # 5. Build n8n payload and POST
    target_platform = action["target_platform"]

    # Detect calendar actions that the stager tagged as "gmail" and reroute
    if target_platform == "gmail" and payload.get("action") == "create_calendar_event":
        target_platform = "google_calendar"

    n8n_config = get_n8n_config()
    base_url = n8n_config["base_url"].rstrip("/")

    # Resolve executor webhook: prefer space-specific executor, fall back to global config
    webhook_path = await _resolve_executor_webhook(db, card_id, target_platform)
    if not webhook_path:
        webhooks = n8n_config.get("webhooks", {})
        webhook_path = webhooks.get(target_platform, f"{target_platform}-executor")
    webhook_url = f"{base_url}/webhook/{webhook_path}"

    # Fetch original event context so executor workflows have enough info to act
    event_rows = await db.execute_fetchall(
        """SELECT actor_email, actor_name, subject_title, source_platform,
                  content_metadata
           FROM events WHERE event_id = ?""",
        (card_row["event_id"],),
    )
    event_ctx = dict(event_rows[0]) if event_rows else {}

    # Resolve actor email — fall back to content_metadata (e.g. gmail_from)
    actor_email = event_ctx.get("actor_email") or ""
    if not actor_email:
        try:
            meta = json.loads(event_ctx.get("content_metadata") or "{}")
            actor_email = meta.get("gmail_from") or meta.get("from") or ""
        except (json.JSONDecodeError, AttributeError):
            pass

    # Normalise payload: coerce None → "" for string fields to prevent n8n
    # JS nodes crashing on undefined.trim(), and map common LLM field-name
    # variants to the canonical keys expected by executor workflows.
    for key in ("body", "subject", "to", "message", "comment", "content", "title", "description"):
        if key in payload and payload[key] is None:
            payload[key] = ""

    # Gmail actions: ensure "body" exists — LLMs sometimes use alternative key names
    if target_platform == "gmail" and "body" not in payload:
        payload["body"] = (
            payload.pop("message", None)
            or payload.pop("content", None)
            or payload.pop("text", None)
            or payload.pop("reply_body", None)
            or payload.pop("email_body", None)
            or payload.pop("reply", None)
            or ""
        )

    n8n_payload = {
        "action_id": action_id,
        "source_event_id": card_row["event_id"],
        "target": {"platform": target_platform, "connection_id": None},
        "action_type": action["action_type"],
        "payload": payload,
        "event_actor_email": actor_email,
        "event_actor_name": event_ctx.get("actor_name", ""),
        "event_subject": event_ctx.get("subject_title", ""),
        "event_platform": event_ctx.get("source_platform", target_platform),
    }

    result_status = "done"
    result_data: dict = {}
    error_message: str | None = None
    result_url: str | None = None
    retryable = False

    # Auto-retry transient n8n failures (2 attempts, 3s backoff)
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=3),
        stop=tenacity.stop_after_attempt(2),
        retry=tenacity.retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _post_to_n8n():
        return await get_client().post(webhook_url, json=n8n_payload, timeout=30.0)

    try:
        resp = await _post_to_n8n()
        try:
            resp_data = resp.json()
        except Exception:
            result_status = "failed"
            error_message = f"n8n returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
            resp_data = None

        if resp_data is not None:
            if resp_data.get("success"):
                result_status = "done"
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
        retryable = True
    except httpx.ConnectError:
        result_status = "failed"
        error_message = "n8n unreachable (connection refused)"
        retryable = True
    except Exception as e:
        result_status = "failed"
        error_message = str(e)

    # 6. Store in action_log and update card — always runs even on unexpected errors
    now = datetime.now(timezone.utc).isoformat()
    try:
        # Upsert action_log so retries don't fail on UNIQUE constraint
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
                json.dumps(result_data) if result_data else None,
                error_message,
                json.dumps(modifications) if modifications else None,
                retryable,
            ),
        )
    except Exception as db_err:
        log.error("action_log_write_failed", card_id=card_id, error=str(db_err))

    # 7. Update card final status — separate try so it always runs even if action_log write fails
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
        error=error_message,
        webhook_url=webhook_url,
    )

    return {
        "card_id": card_id,
        "action_id": action_id,
        "status": result_status,
        "result_url": result_url,
        "error": error_message,
    }


async def _resolve_executor_webhook(
    db, card_id: str, target_platform: str
) -> str | None:
    """Resolve the best executor webhook_path for a card based on its space.

    Priority:
    1. Executor source in the same space as the card → use it (pick any if multiple)
    2. No executor in the same space → pick any executor for this platform
    3. No executor sources registered at all → return None (fall back to global config)
    """
    # Get the card's space_id
    rows = await db.execute_fetchall(
        "SELECT space_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    card_space_id = rows[0]["space_id"] if rows and rows[0]["space_id"] else None

    # Find all executor sources for this platform
    executor_rows = await db.execute_fetchall(
        """SELECT webhook_path, space_id FROM sources
           WHERE source_type = 'executor' AND platform = ? AND webhook_path IS NOT NULL""",
        (target_platform,),
    )

    if not executor_rows:
        return None

    # Try to find one in the same space
    if card_space_id:
        same_space = [r for r in executor_rows if r["space_id"] == card_space_id]
        if same_space:
            chosen = same_space[0]["webhook_path"]
            log.info(
                "executor_resolved",
                card_id=card_id,
                space_id=card_space_id,
                webhook_path=chosen,
                match="same_space",
            )
            return chosen

    # Fall back to any executor for this platform
    chosen = executor_rows[0]["webhook_path"]
    log.info(
        "executor_resolved",
        card_id=card_id,
        space_id=card_space_id,
        webhook_path=chosen,
        match="any_space",
    )
    return chosen
