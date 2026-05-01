"""Workspace API — fetch agent session state for a card."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.agents import session_manager
from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.models.workspace import (
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
)

log = structlog.get_logger()
router = APIRouter()


def _utc_iso(ts: str | None) -> str | None:
    """Ensure SQLite CURRENT_TIMESTAMP values have a Z suffix for proper UTC parsing in browsers."""
    if ts is None:
        return None
    ts = ts.strip()
    if not ts.endswith("Z") and "+" not in ts and not ts.endswith("00:00"):
        return ts + "Z"
    return ts


@router.get("/cards/{card_id}/workspace")
async def get_workspace(card_id: str) -> dict[str, Any]:
    """Fetch workspace state for a card.

    Returns the most recent session and its timeline events,
    plus context extracted from the router output.
    """
    db = await get_db()

    # Get the most recent session for this card
    session_row = await db.execute_fetchall(
        """SELECT session_id, card_id, agent_type, status,
                  repo_path, initial_prompt, started_at, updated_at,
                  completed_at, findings_json, error_message, add_dirs,
                  session_type
           FROM workspace_sessions
           WHERE card_id = ?
           ORDER BY started_at DESC
           LIMIT 1""",
        (card_id,),
    )

    if not session_row:
        # Fall back to entity-level session: look up this card's entity_id,
        # then find a workspace_session by entity_id.
        entity_rows = await db.execute_fetchall(
            "SELECT entity_id FROM action_cards WHERE card_id = ?", (card_id,),
        )
        if entity_rows and entity_rows[0]["entity_id"]:
            session_row = await db.execute_fetchall(
                """SELECT session_id, card_id, agent_type, status,
                          repo_path, initial_prompt, started_at, updated_at,
                          completed_at, findings_json, error_message, add_dirs,
                          session_type
                   FROM workspace_sessions
                   WHERE entity_id = ?
                   ORDER BY started_at DESC
                   LIMIT 1""",
                (entity_rows[0]["entity_id"],),
            )
    if not session_row:
        return {"card_id": card_id, "session": None, "events": [], "context": {}}

    row = session_row[0]
    session = {
        "session_id": row[0],
        "agent_type": row[2],
        "status": row[3],
        "repo_path": row[4],
        "started_at": _utc_iso(row[6]),
        "updated_at": _utc_iso(row[7]),
        "completed_at": _utc_iso(row[8]),
        "findings": json.loads(row[9]) if row[9] else None,
        "error_message": row[10],
        "add_dirs": json.loads(row[11]) if row[11] else [],
        # Detect research sessions: explicit column value, or infer from repo_path
        # for sessions created before the session_type column was added.
        "session_type": row[12] or (
            "research" if row[4] and "/tmp/research/" in row[4] else "code"
        ),
    }

    # Get workspace events for this session
    event_rows = await db.execute_fetchall(
        """SELECT event_id, timestamp, event_type, actor, content, requires_input, agent_message_id
           FROM workspace_events
           WHERE session_id = ?
           ORDER BY timestamp ASC""",
        (row[0],),
    )

    events = [
        {
            "event_id": e[0],
            "timestamp": _utc_iso(e[1]),
            "event_type": e[2],
            "actor": e[3],
            "content": json.loads(e[4]) if e[4] else {},
            "requires_input": bool(e[5]),
            "agent_message_id": e[6],
        }
        for e in event_rows
    ]

    # Build context from the router output stored on the event
    context = await _build_context(card_id)

    return {
        "card_id": card_id,
        "session": session,
        "events": events,
        "context": context,
    }


async def _build_context(card_id: str) -> dict[str, Any]:
    """Build context from the card's router output (entities, research plan)."""
    db = await get_db()

    # Get router_output from the events table via the action_cards FK
    card_rows = await db.execute_fetchall(
        """SELECT e.router_output
           FROM action_cards ac
           JOIN events e ON ac.event_id = e.event_id
           WHERE ac.card_id = ?""",
        (card_id,),
    )

    if not card_rows or not card_rows[0][0]:
        return {}

    try:
        router_output = json.loads(card_rows[0][0])
        context: dict[str, Any] = {}

        entities = router_output.get("entities", [])
        if entities:
            context["related_entities"] = entities

        research_plan = router_output.get("research_plan", [])
        if research_plan:
            context["research_plan"] = research_plan

        return context
    except (json.JSONDecodeError, AttributeError):
        return {}


class AnswerQuestionRequest(BaseModel):
    """Request body for POST /workspace/{session_id}/answer."""
    answers: list[dict[str, Any]]  # [{question_index: 0, selected: "Free APIs (Recommended)"}]
    add_dirs: list[str] | None = None


class ResumePromptRequest(BaseModel):
    """Request body for POST /workspace/{session_id}/resume."""
    prompt: str
    add_dirs: list[str] | None = None


@router.post("/workspace/{session_id}/answer")
async def answer_agent_question(session_id: str, body: AnswerQuestionRequest) -> dict[str, str]:
    """Submit user answers to an AskUserQuestion and resume the agent.

    Formats the answers as natural text and spawns a resumed Claude Code
    subprocess. The resumed conversation outputs are appended to the same
    workspace session.
    """
    db = await get_db()

    # Look up the session and its card
    rows = await db.execute_fetchall(
        "SELECT card_id, cc_session_id, status FROM workspace_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")

    card_id, cc_session_id, status = rows[0]

    # Format answers as natural language for the LLM
    answer_lines = []
    for ans in body.answers:
        header = ans.get("header", "")
        selected = ans.get("selected", "")
        if header:
            answer_lines.append(f"{header}: {selected}")
        else:
            answer_lines.append(str(selected))
    answer_text = "\n".join(answer_lines)

    # Store the user's response as a workspace event
    user_event = WorkspaceEvent(
        event_id=f"we_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        event_type=WorkspaceEventType.USER_RESPONSE,
        actor=WorkspaceEventActor.USER,
        content={"message": answer_text, "answers": body.answers},
    )
    await session_manager.store_workspace_event(user_event)

    # Update card status back to agent_running
    await db.execute(
        "UPDATE action_cards SET status = 'agent_running', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )
    # Reset session status to running
    await db.execute(
        "UPDATE workspace_sessions SET status = 'running', completed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
        (session_id,),
    )
    await db.commit()
    await manager.broadcast({
        "type": "card_updated",
        "card_id": card_id,
        "payload": {"status": "agent_running"},
    })

    # Resume the agent conversation in the background
    from laya.tasks import create_task as create_tracked_task
    create_tracked_task(_run_resumed_session(session_id, card_id, answer_text, add_dirs=body.add_dirs))

    return {"status": "resumed", "session_id": session_id}


async def _run_resumed_session(
    session_id: str,
    card_id: str,
    answer_text: str,
    add_dirs: list[str] | None = None,
    is_freeform: bool = False,
) -> None:
    """Background task: resume agent, stream events, complete session."""
    try:
        agent = await session_manager.resume_conversation(session_id, answer_text, add_dirs=add_dirs)

        findings: dict[str, Any] = {}
        async for ws_event in agent.stream_events():
            inserted = await session_manager.store_workspace_event(ws_event)
            if not inserted:
                continue  # Duplicate from replayed history

            # Broadcast questions and errors
            if ws_event.event_type == WorkspaceEventType.APPROVAL_REQUEST:
                if ws_event.content.get("ask_user_question"):
                    db = await get_db()
                    await db.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (card_id,),
                    )
                    await db.commit()
                    await manager.broadcast({
                        "type": "card_updated",
                        "card_id": card_id,
                        "payload": {"status": "awaiting_input"},
                    })
                await manager.broadcast({
                    "type": "approval_request",
                    "card_id": card_id,
                    "session_id": session_id,
                    "payload": ws_event.content,
                })
            elif ws_event.event_type == WorkspaceEventType.ERROR:
                await manager.broadcast({
                    "type": "agent_error",
                    "card_id": card_id,
                    "session_id": session_id,
                    "payload": ws_event.content,
                })

            # Capture plan from ExitPlanMode
            if ws_event.event_type == WorkspaceEventType.AGENT_MESSAGE and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")

            if ws_event.event_type == WorkspaceEventType.STATUS_CHANGE:
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

            # Capture cc_session_id if agent provides a new one (fork scenario)
            if hasattr(agent, "cc_session_id") and agent.cc_session_id:
                await session_manager.store_cc_session_id(session_id, agent.cc_session_id)

        # Complete session
        final_status = agent.get_status()
        if final_status == SessionStatus.COMPLETED:
            await session_manager.complete_session(session_id, findings=findings)

            db = await get_db()

            agent_plan = findings.get("agent_plan", "")
            agent_result = findings.get("agent_result", "")
            staged_content = agent_plan or agent_result
            if staged_content:
                staged_type = "agent_plan" if agent_plan else "agent_result"
                await db.execute(
                    "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                    (json.dumps({"type": staged_type, "content": staged_content}), card_id),
                )

            # If there are unanswered questions, keep card as awaiting_input
            # Unless this was a freeform resume — agent completed after user's freeform prompt,
            # so implicitly dismiss any outstanding questions.
            has_unanswered = await session_manager.has_unanswered_questions(session_id)
            if has_unanswered and is_freeform:
                dismiss_event = WorkspaceEvent(
                    event_id=f"we_{uuid.uuid4().hex[:12]}",
                    session_id=session_id,
                    event_type=WorkspaceEventType.QUESTIONS_DISMISSED,
                    actor=WorkspaceEventActor.SYSTEM,
                    content={"message": "Questions auto-dismissed after freeform resume"},
                )
                await session_manager.store_workspace_event(dismiss_event)
                has_unanswered = False
            card_status = "awaiting_input" if has_unanswered else "ready"

            await db.execute(
                "UPDATE action_cards SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (card_status, card_id),
            )
            await db.commit()

            await manager.broadcast({
                "type": "card_updated",
                "card_id": card_id,
                "payload": {"status": card_status},
            })
            await manager.broadcast({
                "type": "agent_completed",
                "card_id": card_id,
                "session_id": session_id,
                "payload": {"findings": findings},
            })
        elif final_status == SessionStatus.AWAITING_INPUT:
            # Agent asked another question — session stays open, don't complete
            log.info("agent_awaiting_more_input", session_id=session_id)
        else:
            error_msg = f"Agent ended with status: {final_status.value}"
            log.error("resumed_session_agent_failed", session_id=session_id, card_id=card_id, error=error_msg)
            await session_manager.complete_session(session_id, error=error_msg)

            # Mark card as failed so it doesn't stay stuck on agent_running
            db = await get_db()
            await db.execute(
                "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (card_id,),
            )
            await db.commit()
            await manager.broadcast({
                "type": "card_updated",
                "card_id": card_id,
                "payload": {"status": "failed"},
            })

    except Exception as e:
        log.error("resumed_session_failed", session_id=session_id, card_id=card_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))

        # Mark card as failed so it doesn't stay stuck on agent_running
        try:
            db = await get_db()
            await db.execute(
                "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (card_id,),
            )
            await db.commit()
            await manager.broadcast({
                "type": "card_updated",
                "card_id": card_id,
                "payload": {"status": "failed"},
            })
        except Exception:
            log.error("resumed_session_card_update_failed", session_id=session_id, card_id=card_id)


@router.post("/workspace/{session_id}/resume")
async def resume_session_with_prompt(session_id: str, body: ResumePromptRequest) -> dict[str, str]:
    """Resume a completed/stopped session with a freeform user prompt.

    Works like answer_agent_question but accepts a plain text prompt instead
    of structured answers.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id, cc_session_id, status FROM workspace_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")

    card_id, cc_session_id, status = rows[0]

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    # Store the user's message as a workspace event
    user_event = WorkspaceEvent(
        event_id=f"we_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        event_type=WorkspaceEventType.USER_RESPONSE,
        actor=WorkspaceEventActor.USER,
        content={"message": prompt},
    )
    await session_manager.store_workspace_event(user_event)

    # Update card status back to agent_running
    await db.execute(
        "UPDATE action_cards SET status = 'agent_running', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )
    # Reset session status to running
    await db.execute(
        "UPDATE workspace_sessions SET status = 'running', completed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
        (session_id,),
    )
    await db.commit()
    await manager.broadcast({
        "type": "card_updated",
        "card_id": card_id,
        "payload": {"status": "agent_running"},
    })

    # Resume the agent conversation in the background
    from laya.tasks import create_task as create_tracked_task
    create_tracked_task(_run_resumed_session(session_id, card_id, prompt, add_dirs=body.add_dirs, is_freeform=True))

    return {"status": "resumed", "session_id": session_id}


@router.post("/workspace/{session_id}/dismiss-questions")
async def dismiss_questions(session_id: str) -> dict[str, str]:
    """Dismiss all pending AskUserQuestion prompts for a session.

    Transitions the card from awaiting_input to ready without answering.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT card_id, status FROM workspace_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")

    card_id, session_status = rows[0]

    # Only allow dismissal when the session is not actively running
    if session_status == "running":
        raise HTTPException(status_code=409, detail="Cannot dismiss questions while agent is running")

    # Store the dismiss event
    dismiss_event = WorkspaceEvent(
        event_id=f"we_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        event_type=WorkspaceEventType.QUESTIONS_DISMISSED,
        actor=WorkspaceEventActor.USER,
        content={"message": "Questions dismissed by user"},
    )
    await session_manager.store_workspace_event(dismiss_event)

    # Transition card to ready
    await db.execute(
        "UPDATE action_cards SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )
    # Mark session as completed if it was awaiting_input
    if session_status in ("awaiting_input", "starting"):
        await db.execute(
            "UPDATE workspace_sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
    await db.commit()

    await manager.broadcast({
        "type": "card_updated",
        "card_id": card_id,
        "payload": {"status": "ready"},
    })

    return {"status": "dismissed", "session_id": session_id}


# ---------- Research file browsing ----------

_RESEARCH_ROOT: str | None = None


def _get_research_root() -> str:
    """Return the resolved research directory root (~/.laya/tmp/research/)."""
    global _RESEARCH_ROOT
    if _RESEARCH_ROOT is None:
        from laya.config import LAYA_HOME
        _RESEARCH_ROOT = str((LAYA_HOME / "tmp" / "research").resolve())
    return _RESEARCH_ROOT


def _validate_research_path(path: str) -> str:
    """Resolve a path and verify it's inside the research directory.

    Prevents directory traversal attacks (e.g. ../../etc/passwd).
    Returns the resolved absolute path.
    """
    from pathlib import Path
    resolved = str(Path(path).resolve())
    root = _get_research_root()
    if not resolved.startswith(root + "/") and resolved != root:
        raise HTTPException(status_code=403, detail="Access denied: path is outside the research directory")
    return resolved


@router.get("/workspace/research-files/{card_id}")
async def list_research_files(card_id: str) -> dict:
    """List files in a research session's working directory.

    Only works for research sessions — returns 404 for code sessions.
    Scoped to ~/.laya/tmp/research/ for security.

    For entity-level sessions the research dir is an add_dir (not
    repo_path), so we check add_dirs for a path inside the research root.
    Also falls back to entity_id-based session lookup when no session
    is found directly by card_id.
    """
    from pathlib import Path

    db = await get_db()

    # Try card_id first, then fall back to entity_id
    rows = await db.execute_fetchall(
        "SELECT repo_path, session_type, add_dirs FROM workspace_sessions WHERE card_id = ? ORDER BY started_at DESC LIMIT 1",
        (card_id,),
    )
    if not rows:
        entity_rows = await db.execute_fetchall(
            "SELECT entity_id FROM action_cards WHERE card_id = ?", (card_id,),
        )
        if entity_rows and entity_rows[0]["entity_id"]:
            rows = await db.execute_fetchall(
                "SELECT repo_path, session_type, add_dirs FROM workspace_sessions WHERE entity_id = ? ORDER BY started_at DESC LIMIT 1",
                (entity_rows[0]["entity_id"],),
            )
    if not rows:
        raise HTTPException(status_code=404, detail="No session found for this card")

    repo_path = rows[0]["repo_path"] or ""
    st = rows[0]["session_type"]
    add_dirs_json = rows[0]["add_dirs"]
    is_research = st == "research" or (not st and "/tmp/research/" in repo_path)
    if not is_research:
        raise HTTPException(status_code=403, detail="File browsing is only available for research sessions")

    # Resolve the research directory: could be repo_path itself or an add_dir
    research_root = _get_research_root()
    research_dir_path = None

    if repo_path and repo_path.startswith(research_root):
        research_dir_path = repo_path
    elif add_dirs_json:
        add_dirs = json.loads(add_dirs_json) if isinstance(add_dirs_json, str) else []
        for d in add_dirs:
            if d.startswith(research_root):
                research_dir_path = d
                break

    if not research_dir_path:
        return {"card_id": card_id, "files": []}

    resolved = _validate_research_path(research_dir_path)
    research_dir = Path(resolved)
    if not research_dir.exists():
        return {"card_id": card_id, "files": []}

    files = []
    for f in sorted(research_dir.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(research_dir))
            files.append({
                "name": f.name,
                "path": rel,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })

    return {"card_id": card_id, "files": files}


@router.get("/workspace/research-files/{card_id}/read")
async def read_research_file(card_id: str, path: str) -> dict:
    """Read a file from a research session's working directory.

    Only works for research sessions. The `path` query parameter is
    relative to the research directory. Scoped to ~/.laya/tmp/research/
    for security.
    """
    from pathlib import Path

    if not path:
        raise HTTPException(status_code=400, detail="Missing 'path' query parameter")

    db = await get_db()

    # Try card_id first, then fall back to entity_id
    rows = await db.execute_fetchall(
        "SELECT repo_path, session_type, add_dirs FROM workspace_sessions WHERE card_id = ? ORDER BY started_at DESC LIMIT 1",
        (card_id,),
    )
    if not rows:
        entity_rows = await db.execute_fetchall(
            "SELECT entity_id FROM action_cards WHERE card_id = ?", (card_id,),
        )
        if entity_rows and entity_rows[0]["entity_id"]:
            rows = await db.execute_fetchall(
                "SELECT repo_path, session_type, add_dirs FROM workspace_sessions WHERE entity_id = ? ORDER BY started_at DESC LIMIT 1",
                (entity_rows[0]["entity_id"],),
            )
    if not rows:
        raise HTTPException(status_code=404, detail="No session found for this card")

    repo_path = rows[0]["repo_path"] or ""
    st = rows[0]["session_type"]
    add_dirs_json = rows[0]["add_dirs"]
    is_research = st == "research" or (not st and "/tmp/research/" in repo_path)
    if not is_research:
        raise HTTPException(status_code=403, detail="File reading is only available for research sessions")

    # Resolve the research directory
    research_root = _get_research_root()
    research_dir_path = None
    if repo_path and repo_path.startswith(research_root):
        research_dir_path = repo_path
    elif add_dirs_json:
        add_dirs = json.loads(add_dirs_json) if isinstance(add_dirs_json, str) else []
        for d in add_dirs:
            if d.startswith(research_root):
                research_dir_path = d
                break

    if not research_dir_path:
        raise HTTPException(status_code=404, detail="No research directory for this session")

    # Resolve and validate the full file path
    full_path = _validate_research_path(str(Path(research_dir_path) / path))
    file_path = Path(full_path)

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Read with size limit (2 MB) to prevent memory issues
    MAX_SIZE = 2 * 1024 * 1024
    if file_path.stat().st_size > MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 2 MB)")

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File is not valid UTF-8 text")

    return {
        "path": path,
        "name": file_path.name,
        "content": content,
    }
