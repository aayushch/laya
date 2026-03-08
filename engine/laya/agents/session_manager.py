"""Session manager for coding agent sessions.

Tracks active sessions, handles lifecycle, persists state to SQLite.
Module-level singleton following the same pattern as ConnectionManager.
"""

from __future__ import annotations

import json
import uuid

import structlog

from laya.agents.base import CodingAgent
from laya.agents.claude_code import ClaudeCodeAgent
from laya.agents.codex_cli import CodexCliAgent
from laya.agents.gemini_cli import GeminiCliAgent
from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.models.workspace import AgentType, SessionStatus, WorkspaceEvent

log = structlog.get_logger()

# Active sessions: session_id -> CodingAgent instance
_active_sessions: dict[str, CodingAgent] = {}
# Reverse mapping: card_id -> session_id (for cancellation on card archive/delete)
_card_sessions: dict[str, str] = {}


def _create_agent(agent_type: AgentType) -> CodingAgent:
    """Factory: create the appropriate CodingAgent adapter."""
    match agent_type:
        case AgentType.CLAUDE_CODE:
            return ClaudeCodeAgent()
        case AgentType.GEMINI_CLI:
            return GeminiCliAgent()
        case AgentType.CODEX_CLI:
            return CodexCliAgent()
        case _:
            raise ValueError(f"Unknown agent type: {agent_type}")


def get_configured_agent_type() -> AgentType:
    """Read the configured coding agent from settings.json."""
    settings = load_settings()
    agent_str = settings.get("coding_agent", "claude_code")
    return AgentType(agent_str)


async def start_session(
    card_id: str,
    prompt: str,
    repo_path: str,
    agent_type: AgentType | None = None,
) -> tuple[str, CodingAgent]:
    """Create and start a new agent session.

    Returns:
        Tuple of (session_id, CodingAgent instance).
    """
    if agent_type is None:
        agent_type = get_configured_agent_type()

    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # Persist session to SQLite
    db = await get_db()
    await db.execute(
        """INSERT INTO workspace_sessions
           (session_id, card_id, agent_type, status, repo_path, initial_prompt)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, card_id, agent_type.value, SessionStatus.STARTING.value, repo_path, prompt),
    )
    await db.commit()

    # Create and start the agent
    agent = _create_agent(agent_type)
    await agent.start_session(session_id, prompt, repo_path)
    _active_sessions[session_id] = agent
    _card_sessions[card_id] = session_id

    # Update status to running
    await _update_session_status(session_id, SessionStatus.RUNNING)

    log.info("session_started", session_id=session_id, card_id=card_id, agent_type=agent_type.value)
    return session_id, agent


def get_session(session_id: str) -> CodingAgent | None:
    """Get an active agent session by ID."""
    return _active_sessions.get(session_id)


async def send_input(session_id: str, text: str) -> None:
    """Send user input to an active session."""
    agent = _active_sessions.get(session_id)
    if agent is None:
        raise ValueError(f"No active session: {session_id}")
    await agent.send_input(text)
    log.info("session_input_sent", session_id=session_id)


async def pause_session(session_id: str) -> None:
    """Pause an active session."""
    agent = _active_sessions.get(session_id)
    if agent:
        await agent.pause()
        await _update_session_status(session_id, SessionStatus.PAUSED)


async def resume_session(session_id: str) -> None:
    """Resume a paused session."""
    agent = _active_sessions.get(session_id)
    if agent:
        await agent.resume()
        await _update_session_status(session_id, SessionStatus.RUNNING)


async def cancel_session(session_id: str) -> None:
    """Cancel an active session."""
    agent = _active_sessions.get(session_id)
    if agent:
        await agent.cancel()
        await _update_session_status(session_id, SessionStatus.CANCELLED)
        _active_sessions.pop(session_id, None)
        # Clean up reverse mapping
        for cid, sid in list(_card_sessions.items()):
            if sid == session_id:
                _card_sessions.pop(cid, None)
                break


async def cancel_sessions_for_card(card_id: str) -> None:
    """Cancel any active agent session associated with a card."""
    session_id = _card_sessions.get(card_id)
    if session_id and session_id in _active_sessions:
        log.info("cancelling_session_for_card", card_id=card_id, session_id=session_id)
        await cancel_session(session_id)


async def complete_session(
    session_id: str,
    findings: dict | None = None,
    error: str | None = None,
) -> None:
    """Mark a session as completed or failed and store findings."""
    db = await get_db()
    status = SessionStatus.FAILED if error else SessionStatus.COMPLETED
    await db.execute(
        """UPDATE workspace_sessions
           SET status = ?, completed_at = CURRENT_TIMESTAMP,
               findings_json = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
           WHERE session_id = ?""",
        (status.value, json.dumps(findings) if findings else None, error, session_id),
    )
    await db.commit()
    _active_sessions.pop(session_id, None)
    # Clean up reverse mapping
    for cid, sid in list(_card_sessions.items()):
        if sid == session_id:
            _card_sessions.pop(cid, None)
            break


async def store_workspace_event(event: WorkspaceEvent) -> bool:
    """Persist a workspace event to SQLite.

    Always inserts — duplicates from --resume replays are handled
    client-side during rendering.
    Returns True (always inserted).
    """
    db = await get_db()
    await db.execute(
        """INSERT INTO workspace_events
           (event_id, session_id, event_type, actor, content, requires_input, agent_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id,
            event.session_id,
            event.event_type.value,
            event.actor.value,
            json.dumps(event.content),
            event.requires_input,
            event.agent_message_id,
        ),
    )
    await db.commit()
    return True


async def has_unanswered_questions(session_id: str) -> bool:
    """Check if a session has approval_request events with ask_user_question
    that have not been followed by a user_response."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT event_id, event_type, content FROM workspace_events
           WHERE session_id = ? ORDER BY timestamp ASC""",
        (session_id,),
    )
    unanswered = 0
    for row in rows:
        event_type = row[1]
        content = json.loads(row[2]) if row[2] else {}
        if event_type == "approval_request" and content.get("ask_user_question"):
            unanswered += 1
        elif event_type == "user_response" and unanswered > 0:
            unanswered -= 1
    return unanswered > 0


async def store_cc_session_id(session_id: str, cc_session_id: str) -> None:
    """Store Claude Code's session UUID for conversation resumption."""
    db = await get_db()
    # Only write once — avoid redundant updates on every event
    result = await db.execute_fetchall(
        "SELECT cc_session_id FROM workspace_sessions WHERE session_id = ?",
        (session_id,),
    )
    if result and result[0][0] != cc_session_id:
        await db.execute(
            "UPDATE workspace_sessions SET cc_session_id = ? WHERE session_id = ?",
            (cc_session_id, session_id),
        )
        await db.commit()
        log.info("cc_session_id_stored", session_id=session_id, cc_session_id=cc_session_id)


async def resume_conversation(
    session_id: str,
    answer_text: str,
    add_dirs: list[str] | None = None,
) -> CodingAgent:
    """Resume an agent conversation with the user's answer.

    Looks up the cc_session_id and repo_path from the DB, creates a new
    ClaudeCodeAgent, and spawns it with --resume. The caller should
    stream_events() and store them in the same workspace session.

    Args:
        add_dirs: Extra directory paths to pass via --add-dir flags.

    Returns:
        The resumed CodingAgent instance.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT cc_session_id, repo_path, agent_type FROM workspace_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not rows:
        raise ValueError(f"No session found: {session_id}")

    cc_session_id, repo_path, agent_type_str = rows[0]
    if not cc_session_id:
        raise ValueError(f"No Claude Code session ID for session: {session_id}")

    agent = ClaudeCodeAgent()
    agent._session_id = session_id
    agent._cc_session_id = cc_session_id
    agent._repo_path = repo_path

    await agent.resume_with_answer(answer_text, add_dirs=add_dirs)

    # Update session status back to running
    await _update_session_status(session_id, SessionStatus.RUNNING)
    _active_sessions[session_id] = agent

    log.info("session_resumed", session_id=session_id, cc_session_id=cc_session_id)
    return agent


async def _update_session_status(session_id: str, status: SessionStatus) -> None:
    """Update session status in SQLite."""
    db = await get_db()
    await db.execute(
        "UPDATE workspace_sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
        (status.value, session_id),
    )
    await db.commit()


def get_active_session_ids() -> list[str]:
    """Return all active session IDs."""
    return list(_active_sessions.keys())


async def cleanup_on_shutdown() -> None:
    """Cancel all active sessions during engine shutdown."""
    for session_id in list(_active_sessions.keys()):
        try:
            await cancel_session(session_id)
        except Exception as e:
            log.warning("session_cleanup_failed", session_id=session_id, error=str(e))
