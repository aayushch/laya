# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Agent-run + file-upload endpoints and the agent event-stream drivers (split from cards_api — P7-6)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.tasks import create_task as create_tracked_task

log = structlog.get_logger()
router = APIRouter()


class RunAgentRequest(BaseModel):
    prompt: str
    directory: str | None = None  # If omitted, defaults to ~/.laya/tmp/research/<card_id>/
    add_dirs: list[str] | None = None
    agent_type: str | None = None  # claude_code, gemini_cli, codex_cli
    mode: str | None = None  # e.g. plan, acceptEdits (claude), read-only, full-auto (codex)
    space_id: str | None = None
    files: list[str] | None = None  # Absolute paths to uploaded staging files



async def _run_agent_session_stream(
    session_id: str,
    agent: "Any",
    broadcast_card_id: str,
    *,
    log_prefix: str = "agent",
) -> None:
    """Stream an agent session's events to its card and finalize its status.

    The body shared by _stream_agent_to_card (single-card run_agent) and
    _stream_entity_agent (entity-level run): consume ``agent.stream_events()``,
    persist each event, surface approval-requests/errors over the websocket, and
    on completion persist any staged plan/result and transition the card to
    ready / awaiting_input / failed. The two callers were byte-identical here
    apart from which card_id the broadcasts key on and their log-event prefix
    (review §5 — P7-6). Callers own any pre-stream spawn + agent_running
    transition.
    """
    from laya.agents import session_manager
    from laya.models.workspace import SessionStatus

    findings: dict[str, Any] = {}
    cc_session_id_stored = False

    try:
        async for ws_event in agent.stream_events():
            inserted = await session_manager.store_workspace_event(ws_event)
            if not inserted:
                continue

            if not cc_session_id_stored and hasattr(agent, "cc_session_id") and agent.cc_session_id:
                await session_manager.store_cc_session_id(session_id, agent.cc_session_id)
                cc_session_id_stored = True

            if ws_event.event_type.value == "approval_request":
                if ws_event.content.get("ask_user_question"):
                    db_aw = await get_db()
                    await db_aw.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (broadcast_card_id,),
                    )
                    await db_aw.commit()
                    await manager.broadcast(
                        {"type": "card_updated", "card_id": broadcast_card_id, "payload": {"status": "awaiting_input"}}
                    )
                await manager.broadcast(
                    {"type": "approval_request", "card_id": broadcast_card_id, "session_id": session_id, "payload": ws_event.content}
                )
            elif ws_event.event_type.value == "error":
                findings["last_error"] = ws_event.content.get("error", "")
                await manager.broadcast(
                    {"type": "agent_error", "card_id": broadcast_card_id, "session_id": session_id, "payload": ws_event.content}
                )

            if ws_event.event_type.value == "agent_message" and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")
            if ws_event.event_type.value == "status_change":
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error(f"{log_prefix}_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        db_err = await get_db()
        await db_err.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (broadcast_card_id,),
        )
        await db_err.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": broadcast_card_id, "payload": {"status": "failed"}}
        )
        return

    # Complete session and update card status
    final_status = agent.get_status()
    db_fin = await get_db()

    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

        agent_plan = findings.get("agent_plan", "")
        agent_result = findings.get("agent_result", "")
        staged_content = agent_plan or agent_result
        if staged_content:
            staged_type = "agent_plan" if agent_plan else "agent_result"
            await db_fin.execute(
                "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (json.dumps({"type": staged_type, "content": staged_content}), broadcast_card_id),
            )

        has_unanswered = await session_manager.has_unanswered_questions(session_id)
        card_status = "awaiting_input" if has_unanswered else "ready"

        await db_fin.execute(
            "UPDATE action_cards SET status = ?, failed_stage = NULL, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_status, broadcast_card_id),
        )
        await db_fin.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": broadcast_card_id, "payload": {"status": card_status}}
        )
        await manager.broadcast(
            {"type": "agent_completed", "card_id": broadcast_card_id, "session_id": session_id, "payload": {"findings": findings}}
        )
    elif final_status == SessionStatus.CANCELLED:
        await session_manager.complete_session(session_id, error="Cancelled by user")
        await db_fin.execute(
            "UPDATE action_cards SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (broadcast_card_id,),
        )
        await db_fin.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": broadcast_card_id, "payload": {"status": "ready"}}
        )
    else:
        last_error = findings.get("last_error", "")
        error_msg = f"Agent ended with status: {final_status.value}"
        if last_error:
            error_msg += f" — {last_error}"
        log.error(f"{log_prefix}_failed", session_id=session_id, error=error_msg)
        await session_manager.complete_session(session_id, error=error_msg)
        await db_fin.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_execution', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (broadcast_card_id,),
        )
        await db_fin.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": broadcast_card_id, "payload": {"status": "failed"}}
        )


async def _stream_agent_to_card(
    card_id: str,
    prompt: str,
    directory: str,
    agent_type: "Any",
    space_id: str | None = None,
    add_dirs: list[str] | None = None,
    mode: str | None = None,
    research: bool = False,
) -> None:
    """Background task for run_agent(): spawn the agent, flip the card to
    agent_running, then delegate the event stream + finalization to
    _run_agent_session_stream (review §5 — P7-6)."""
    from laya.agents import session_manager

    try:
        session_id, agent = await session_manager.start_session(
            card_id=card_id,
            prompt=prompt,
            repo_path=directory,
            agent_type=agent_type,
            space_id=space_id,
            add_dirs=add_dirs,
            mode=mode,
            research=research,
        )
    except Exception as e:
        log.error("agent_spawn_failed", card_id=card_id, error=str(e))
        db2 = await get_db()
        await db2.execute(
            "UPDATE action_cards SET status = 'failed', failed_stage = 'agent_spawn', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db2.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Update card status to agent_running and broadcast
    db_run = await get_db()
    await db_run.execute(
        "UPDATE action_cards SET status = 'agent_running', has_workspace = 1, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )
    await db_run.commit()
    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"has_workspace": True, "status": "agent_running", "session_id": session_id}}
    )

    await _run_agent_session_stream(session_id, agent, card_id, log_prefix="agent")


class UploadAgentFilePathRequest(BaseModel):
    path: str


class DeleteStagingFileRequest(BaseModel):
    path: str


@router.post("/delete-agent-staging-file")
async def delete_agent_staging_file(body: DeleteStagingFileRequest) -> dict:
    """Delete a staged upload that the user removed before submitting.

    Path must be inside ~/.laya/tmp/agent-staging/; anything else is rejected
    to prevent path-traversal abuse. Missing files are treated as success
    (idempotent — safe to call twice).
    """
    import os
    from pathlib import Path as _Path

    from laya.config import LAYA_HOME

    # Normalize both paths lexically with os.path.normpath (collapses '..', no
    # filesystem access) rather than Path.resolve(). resolve() stat()s the path,
    # which CodeQL's py/path-injection flags as an unguarded sink *before* the
    # relative_to containment check below. Lexical normalization keeps the
    # relative_to call the only operation that touches this user-supplied path,
    # and matches how the upload endpoints build staging paths (from the same
    # un-resolved LAYA_HOME base), so legit deletes still resolve correctly.
    staging_dir = _Path(os.path.normpath(LAYA_HOME / "tmp" / "agent-staging"))
    target = _Path(os.path.normpath(_Path(body.path).expanduser()))

    try:
        target.relative_to(staging_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path is not inside the staging directory")

    if target.exists() and target.is_file():
        try:
            target.unlink()
            log.info("agent_staging_file_deleted", path=str(target))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"status": "ok"}


@router.post("/upload-agent-file-path")
async def upload_agent_file_path(body: UploadAgentFilePathRequest) -> dict:
    """Stage a reference file by local path.

    Tauri v2 on macOS (WKWebView) doesn't propagate OS-level file drops to the
    DOM, so the frontend uses Tauri's native drag-drop event which gives us
    absolute paths instead of File blobs. Since the engine runs locally, we can
    copy from that path into staging directly.
    """
    from pathlib import Path as _Path
    import mimetypes
    import shutil
    import uuid as _uuid

    from laya.config import LAYA_HOME

    src = _Path(body.path).expanduser()
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    staging_dir = LAYA_HOME / "tmp" / "agent-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    ext = "".join(c for c in src.suffix.lstrip(".").lower() if c.isalnum())[:8] or "bin"
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    dst = staging_dir / filename
    shutil.copy2(src, dst)

    content_type, _ = mimetypes.guess_type(str(src))
    log.info("agent_file_staged_by_path", src=str(src), dst=str(dst))
    return {
        "path": str(dst),
        "filename": src.name,  # preserve the user's original filename for the UI tile
        "size": dst.stat().st_size,
        "content_type": content_type or "application/octet-stream",
    }


@router.post("/upload-agent-file")
async def upload_agent_file(file: UploadFile = File(...)) -> dict:
    """Upload a reference file (image, PDF, text, etc.) for use with an agent run.

    Writes the POST body to ~/.laya/tmp/agent-staging/<uuid>.<ext> — a server-side
    staging area. On run-agent submit, the staged copy is moved into the card's
    attachments folder. The user's original file on disk is never touched.
    """
    from laya.config import LAYA_HOME

    staging_dir = LAYA_HOME / "tmp" / "agent-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Extension resolution: preserve original (sanitized) if present, else MIME-map.
    ext = ""
    if file.filename:
        parts = file.filename.rsplit(".", 1)
        if len(parts) == 2:
            candidate = "".join(c for c in parts[1].lower() if c.isalnum())[:8]
            if candidate:
                ext = candidate
    if not ext and file.content_type:
        ct_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/svg+xml": "svg",
            "application/pdf": "pdf",
            "text/plain": "txt",
            "text/csv": "csv",
            "application/json": "json",
            "text/markdown": "md",
        }
        ext = ct_map.get(file.content_type, "")
    if not ext:
        ext = "bin"

    import uuid as _uuid
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    filepath = staging_dir / filename

    content = await file.read()
    filepath.write_bytes(content)

    log.info("agent_file_uploaded", path=str(filepath), size=len(content), content_type=file.content_type)
    return {
        "path": str(filepath),
        "filename": filename,
        "size": len(content),
        "content_type": file.content_type or "application/octet-stream",
    }


@router.post("/cards/run-agent")
async def run_agent(body: RunAgentRequest) -> dict:
    """Create an ENGINEER card and spawn a coding agent directly.

    User-initiated agent run (triggered from the 'a' keyboard shortcut).
    Creates a card with source=laya, persona=ENGINEER, and immediately
    spawns the agent subprocess. The card then follows the normal workspace flow.
    """
    from pathlib import Path

    from laya.agents import session_manager
    from laya.config import LAYA_HOME
    from laya.models.workspace import AgentType

    # Resolve agent type
    if body.agent_type:
        try:
            agent_type = AgentType(body.agent_type)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown agent type: {body.agent_type}")
    else:
        agent_type = session_manager.get_configured_agent_type()

    # Generate event + card ids up front — we need card_id to provision the
    # per-card attachments folder before the agent spawns.
    import uuid
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    now = db_now()
    header = body.prompt[:120] + ("..." if len(body.prompt) > 120 else "")
    entity_id = f"laya:agent_run:{card_id}"

    # Resolve working directory. If the caller didn't specify one, use
    # ~/.laya/tmp/research/<card_id>/ and enable research mode (scoped writes
    # + web). The path substring '/tmp/research/' also lets workspace_api
    # auto-classify this session as research, which unlocks the file browser.
    card_dir = LAYA_HOME / "tmp" / "research" / card_id
    card_dir.mkdir(parents=True, exist_ok=True)
    if body.directory:
        working_dir = body.directory
        research_flag = False
    else:
        working_dir = str(card_dir)
        research_flag = True

    # Move staged uploads (server-side copies in ~/.laya/tmp/agent-staging/)
    # into card_dir/attachments/. The user's original files on disk are never
    # touched — only the copies the upload endpoint wrote.
    final_file_paths: list[str] = []
    if body.files:
        import os
        staging_dir = Path(os.path.normpath(LAYA_HOME / "tmp" / "agent-staging"))
        attachments_dir = card_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        for staged_path in body.files:
            # Confine to the staging dir: body.files must be paths the upload
            # endpoints returned (uuid-named copies under agent-staging). Normalize
            # lexically (os.path.normpath, no filesystem access) and reject anything
            # outside staging so a crafted path can't rename an arbitrary file into
            # the workspace. relative_to (not startswith) is the containment barrier
            # CodeQL's py/path-injection recognizes.
            src = Path(os.path.normpath(Path(staged_path).expanduser()))
            try:
                src.relative_to(staging_dir)
            except ValueError:
                log.warning("agent_file_move_rejected", path=staged_path, card_id=card_id)
                continue
            if not src.exists():
                log.warning("agent_file_move_missing", path=staged_path, card_id=card_id)
                continue
            dst = attachments_dir / src.name
            try:
                src.rename(dst)
            except OSError as e:
                log.warning("agent_file_move_failed", src=str(src), dst=str(dst), error=str(e))
                continue
            final_file_paths.append(str(dst))

    # Build the effective prompt — append file references so the agent can
    # open them via its Read/file tool (agents don't have --attach flags).
    effective_prompt = body.prompt
    if final_file_paths:
        file_lines = "\n".join(f"- {p}" for p in final_file_paths)
        effective_prompt += (
            f"\n\nAttached reference files (use your Read/file tool to open them):\n{file_lines}"
        )

    db = await get_db()

    # Synthetic event so the FK constraint is satisfied.
    # Mark as 'completed' so the queue consumer never picks it up —
    # the raw_json is a flat dict (not a valid LayaEvent) and would
    # fail deserialization in _load_event().
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, content_body, raw_json,
            processed, processing_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            now,
            "laya",
            "agent_run",
            "agent_task",
            card_id,
            header,
            body.prompt,
            json.dumps({"source": "laya", "type": "agent_run", "prompt": body.prompt}),
            True,
            "completed",
        ),
    )

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            intelligence, staged_output, suggested_actions, status,
            privacy_tier, has_workspace, confidence, entity_id, source_ref,
            space_id, agent_prompt, group_active_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            event_id,
            "MEDIUM",
            "ENGINEER",
            "CODE",
            header,
            body.prompt,  # summary shows the original prompt (without attachment paths)
            json.dumps([]),
            json.dumps({}),
            json.dumps([]),
            "agent_running",
            2,
            True,
            1.0,
            entity_id,
            "Agent Run",
            body.space_id or "default",
            effective_prompt,  # agent_prompt includes attachment references
            now_ts,
        ),
    )
    await db.commit()

    # Broadcast card creation
    await manager.broadcast(
        {
            "type": "card_created",
            "card_id": card_id,
            "payload": {
                "header": header,
                "summary": body.prompt,
                "priority": "MEDIUM",
                "persona": "ENGINEER",
                "category": "CODE",
                "status": "agent_running",
                "has_workspace": True,
                "privacy_tier": 2,
            },
        }
    )

    # Spawn agent in background. Attachments live inside card_dir/attachments/
    # and persist with the card — no post-run cleanup.
    create_tracked_task(
        _stream_agent_to_card(
            card_id=card_id,
            prompt=effective_prompt,
            directory=working_dir,
            agent_type=agent_type,
            space_id=body.space_id,
            add_dirs=body.add_dirs,
            mode=body.mode,
            research=research_flag,
        ),
        name=f"run_agent_{card_id}",
    )

    log.info("run_agent_initiated", card_id=card_id, agent_type=agent_type.value)
    return {"status": "agent_running", "card_id": card_id}


class RunEntityAgentRequest(BaseModel):
    prompt: str | None = None


@router.post("/entity/{entity_id:path}/run-agent")
async def run_entity_agent(entity_id: str, body: RunEntityAgentRequest) -> dict:
    """Start or resume an agent session for an entity group.

    Associates an agent at the entity level rather than per-card.
    Builds CONTEXT.md with group summary + card details, resolves repo,
    and spawns the agent. On subsequent calls, refreshes context and resumes.
    """
    from urllib.parse import unquote

    from laya.agents import session_manager
    from laya.agents.entity_context import (
        build_entity_agent_prompt,
        get_entity_research_dir,
        write_entity_context_file,
    )
    from laya.config import load_repos, load_settings
    from laya.workers.engineer import resolve_repo_path

    entity_id = unquote(entity_id)
    db = await get_db()

    # 1. Fetch all cards for this entity
    card_rows = await db.execute_fetchall(
        "SELECT card_id, status, space_id, entity_id FROM action_cards WHERE entity_id = ? ORDER BY created_at DESC",
        (entity_id,),
    )
    if not card_rows:
        raise HTTPException(status_code=404, detail="No cards found for this entity")

    # 2. Validate agent is configured
    settings = load_settings()
    agent_setting = settings.get("coding_agent", "none")
    if agent_setting == "none":
        raise HTTPException(
            status_code=409,
            detail="No coding agent configured. Set one in Settings > Agent.",
        )

    # 3. Check for an existing session. include_terminal=True so a COMPLETED
    # prior run is resumed rather than replaced — without it get_session_for_entity
    # returned None for a finished session and this handler spawned a duplicate
    # workspace, orphaning the one the user opens via the Workspace button. This
    # now mirrors the processing-rule path (review §1.9 — P3-3).
    existing = await session_manager.get_session_for_entity(entity_id, include_terminal=True)
    if existing and existing["status"] in ("starting", "running"):
        raise HTTPException(
            status_code=409,
            detail="An agent is already running for this entity",
        )
    if existing and (
        existing["status"] == "awaiting_input"
        or await session_manager.has_unanswered_questions(existing["session_id"])
    ):
        raise HTTPException(
            status_code=409,
            detail="The workspace is awaiting your input",
        )

    space_id = card_rows[0]["space_id"] or "default"
    anchor_card_id = card_rows[0]["card_id"]

    # 4. Build CONTEXT.md
    await write_entity_context_file(entity_id, space_id)
    research_dir = get_entity_research_dir(entity_id)

    # 5. Resolve repo
    from laya.models.classification import Category, Persona, Priority, RouterOutput

    dummy_router = RouterOutput(
        persona=Persona.ENGINEER, priority=Priority.MEDIUM,
        category=Category.CODE, confidence=0.8, entities=[],
    )
    repo_path, other_repos = await resolve_repo_path(dummy_router, space_id=space_id)

    # 6. Determine cwd and add_dirs
    research_dir_str = str(research_dir)
    if repo_path:
        cwd = repo_path
        add_dirs = [research_dir_str] + [p for p in other_repos if p != research_dir_str]
    else:
        cwd = research_dir_str
        repos_data = load_repos()
        add_dirs = [r["path"] for r in repos_data.get("repos", []) if r.get("path")]

    # 7. Build agent prompt
    agent_prompt = build_entity_agent_prompt(
        entity_id=entity_id,
        research_dir=research_dir_str,
        repo_path=repo_path,
        user_prompt=body.prompt,
    )

    # 8. If a prior session exists (completed/paused/failed/cancelled — the
    # running/awaiting cases were already rejected above), resume it instead of
    # spawning a duplicate (review §1.9 — P3-3).
    if existing:
        # Refresh context and resume
        now = db_now()
        await db.execute(
            "UPDATE action_cards SET has_workspace = 1, updated_at = ? WHERE entity_id = ?",
            (now, entity_id),
        )
        await db.execute(
            "UPDATE action_cards SET status = 'agent_running' WHERE card_id = ?",
            (anchor_card_id,),
        )
        await db.commit()

        for card_row in card_rows:
            payload: dict = {"has_workspace": True}
            if card_row["card_id"] == anchor_card_id:
                payload["status"] = "agent_running"
            await manager.broadcast(
                {"type": "card_updated", "card_id": card_row["card_id"], "payload": payload}
            )

        resume_text = body.prompt or "Continue working. Check CONTEXT.md for updated entity context."
        agent = await session_manager.resume_conversation(
            existing["session_id"], resume_text, add_dirs=add_dirs,
        )

        create_tracked_task(
            _stream_entity_agent(
                session_id=existing["session_id"],
                agent=agent,
                entity_id=entity_id,
                anchor_card_id=anchor_card_id,
            ),
            name=f"entity_agent_{entity_id}",
        )

        log.info("entity_agent_resumed", entity_id=entity_id, session_id=existing["session_id"])
        return {"status": "agent_running", "session_id": existing["session_id"], "card_id": anchor_card_id}

    # 9. New session — start in plan mode

    now = db_now()
    await db.execute(
        "UPDATE action_cards SET has_workspace = 1, updated_at = ? WHERE entity_id = ?",
        (now, entity_id),
    )
    await db.execute(
        "UPDATE action_cards SET status = 'agent_running' WHERE card_id = ?",
        (anchor_card_id,),
    )
    await db.commit()

    for card_row in card_rows:
        payload: dict = {"has_workspace": True}
        if card_row["card_id"] == anchor_card_id:
            payload["status"] = "agent_running"
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_row["card_id"], "payload": payload}
        )

    # Delegate agent resolution to start_session (explicit > space override > global
    # default). Passing agent_type=None + the space_id lets the per-space coding_agent
    # set in Settings > Spaces take effect; without this it would always use the global.
    session_id, agent = await session_manager.start_session(
        card_id=anchor_card_id,
        prompt=agent_prompt,
        repo_path=cwd,
        agent_type=None,
        space_id=space_id,
        add_dirs=add_dirs,
        mode="plan",
        research=True,
        entity_id=entity_id,
    )

    create_tracked_task(
        _stream_entity_agent(
            session_id=session_id,
            agent=agent,
            entity_id=entity_id,
            anchor_card_id=anchor_card_id,
        ),
        name=f"entity_agent_{entity_id}",
    )

    log.info("entity_agent_started", entity_id=entity_id, session_id=session_id, anchor=anchor_card_id)
    return {"status": "agent_running", "session_id": session_id, "card_id": anchor_card_id}


async def _stream_entity_agent(
    session_id: str,
    agent: "Any",
    entity_id: str,
    anchor_card_id: str,
) -> None:
    """Background task for an entity-level session (run_entity_agent, and the
    processing-rule agent path). The caller has already started the session and
    flipped the entity's cards to agent_running, so this just streams events +
    finalizes, keyed to the anchor card (review §5 — P7-6)."""
    await _run_agent_session_stream(
        session_id, agent, anchor_card_id, log_prefix="entity_agent"
    )


# ---------- Context Group Management ----------
