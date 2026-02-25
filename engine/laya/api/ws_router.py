"""WebSocket message router — handles incoming UI messages."""

from __future__ import annotations

import json
import uuid

import structlog

from laya.agents import session_manager
from laya.models.workspace import WorkspaceEvent, WorkspaceEventActor, WorkspaceEventType

log = structlog.get_logger()


async def handle_ws_message(data: str) -> None:
    """Parse and route an incoming WebSocket message from the UI.

    Message types:
        approve_action  — User approves an agent's pending request
        deny_action     — User denies an agent's pending request
        user_input      — User sends freeform text to the agent
        session_control — User pauses, resumes, or cancels a session
    """
    try:
        msg = json.loads(data)
    except json.JSONDecodeError:
        log.warning("ws_invalid_json", data=data[:200])
        return

    msg_type = msg.get("type")
    session_id = msg.get("session_id")

    if not msg_type:
        log.warning("ws_missing_type", data=data[:200])
        return

    match msg_type:
        case "approve_action":
            await _handle_approve(session_id, msg)
        case "deny_action":
            await _handle_deny(session_id, msg)
        case "user_input":
            await _handle_user_input(session_id, msg)
        case "session_control":
            await _handle_session_control(session_id, msg)
        case "execute_action":
            await _handle_execute_action(msg)
        case "chat_message":
            await _handle_chat_message(msg)
        case _:
            log.debug("ws_unhandled_type", type=msg_type)


async def _handle_approve(session_id: str | None, msg: dict) -> None:
    """Handle approve_action: send 'yes' to the agent and persist event."""
    if not session_id:
        log.warning("ws_approve_no_session")
        return

    await session_manager.send_input(session_id, "yes")
    await _store_user_event(
        session_id=session_id,
        event_type=WorkspaceEventType.APPROVAL_RESPONSE,
        content={"approved": True, **msg.get("payload", {})},
    )
    log.info("ws_action_approved", session_id=session_id)


async def _handle_deny(session_id: str | None, msg: dict) -> None:
    """Handle deny_action: send denial reason to the agent and persist event."""
    if not session_id:
        log.warning("ws_deny_no_session")
        return

    payload = msg.get("payload", {})
    reason = payload.get("reason", "no")
    await session_manager.send_input(session_id, reason)
    await _store_user_event(
        session_id=session_id,
        event_type=WorkspaceEventType.APPROVAL_RESPONSE,
        content={"approved": False, "reason": reason},
    )
    log.info("ws_action_denied", session_id=session_id)


async def _handle_user_input(session_id: str | None, msg: dict) -> None:
    """Handle user_input: pipe freeform text to the agent."""
    if not session_id:
        log.warning("ws_input_no_session")
        return

    payload = msg.get("payload", {})
    message = payload.get("message", "")
    if message:
        await session_manager.send_input(session_id, message)
        await _store_user_event(
            session_id=session_id,
            event_type=WorkspaceEventType.USER_RESPONSE,
            content={"message": message},
        )
    log.info("ws_user_input", session_id=session_id)


async def _handle_session_control(session_id: str | None, msg: dict) -> None:
    """Handle session_control: pause, resume, or cancel."""
    if not session_id:
        log.warning("ws_control_no_session")
        return

    payload = msg.get("payload", {})
    action = payload.get("action")

    match action:
        case "pause":
            await session_manager.pause_session(session_id)
        case "resume":
            await session_manager.resume_session(session_id)
        case "cancel":
            await session_manager.cancel_session(session_id)
        case _:
            log.warning("ws_unknown_control_action", action=action)
            return

    await _store_user_event(
        session_id=session_id,
        event_type=WorkspaceEventType.STATUS_CHANGE,
        content={"action": action},
    )
    log.info("ws_session_control", session_id=session_id, action=action)


async def _handle_execute_action(msg: dict) -> None:
    """Handle execute_action: trigger action execution from workspace."""
    card_id = msg.get("card_id")
    payload = msg.get("payload", {})
    action_id = payload.get("action_id")
    modifications = payload.get("modifications")

    if not card_id or not action_id:
        log.warning("ws_execute_missing_ids", card_id=card_id, action_id=action_id)
        return

    from laya.pipeline.executor import execute_action

    try:
        await execute_action(card_id, action_id, modifications)
        log.info("ws_action_executed", card_id=card_id, action_id=action_id)
    except Exception as e:
        log.error("ws_execute_failed", card_id=card_id, error=str(e))


async def _handle_chat_message(msg: dict) -> None:
    """Handle chat_message: process via chat pipeline and broadcast response."""
    payload = msg.get("payload", {})
    message = payload.get("message", "")

    if not message.strip():
        log.warning("ws_chat_empty_message")
        return

    from laya.api.websocket import manager
    from laya.pipeline.chat import process_chat_message

    try:
        response = await process_chat_message(message.strip())
        await manager.broadcast({
            "type": "chat_response",
            "payload": {
                "message": response.message.model_dump(),
                "referenced_cards": response.referenced_cards,
                "referenced_events": response.referenced_events,
            },
        })
        log.info("ws_chat_response_sent")
    except Exception as e:
        log.error("ws_chat_failed", error=str(e))


async def _store_user_event(
    session_id: str,
    event_type: WorkspaceEventType,
    content: dict,
) -> None:
    """Persist a user-initiated workspace event to SQLite."""
    event = WorkspaceEvent(
        event_id=f"we_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        event_type=event_type,
        actor=WorkspaceEventActor.USER,
        content=content,
    )
    await session_manager.store_workspace_event(event)
