"""ENGINEER Worker — coding agent orchestration for code research and fixes."""

from __future__ import annotations

import json
from typing import Any

import structlog

from laya.agents import session_manager
from laya.api.websocket import manager
from laya.config import load_repos, load_settings
from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.engineer import build_engineer_messages, get_engineer_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.models.workspace import SessionStatus, WorkspaceEventType
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def _get_space_repos(space_id: str) -> list[str]:
    """Get repo names assigned to a space from the space_repos table."""
    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT repo_name FROM space_repos WHERE space_id = ? ORDER BY position",
            (space_id,),
        )
        return [r["repo_name"] for r in rows]
    except Exception:
        return []


async def _resolve_repo_path(
    router_output: RouterOutput,
    event: LayaEvent | None = None,
    space_id: str | None = None,
) -> str | None:
    """Resolve the target repo path from space assignment, entities, and repos.json.

    Matching strategy (first match wins):
    0. If space has repos assigned, narrow candidates to those repos.
       If only one repo is assigned to the space, return it immediately.
    1. Exact name match on entity value vs repo name
    2. Substring match on entity value vs repo name or remote_id
    3. Keyword match: scan event subject/body for repo names
    4. Fallback: first repo in the (possibly narrowed) candidate list
    """
    repos_data = load_repos()
    repos = repos_data.get("repos", [])

    if not repos:
        return None

    # 0. Narrow to space-assigned repos if available
    if space_id:
        space_repo_names = await _get_space_repos(space_id)
        if space_repo_names:
            space_repos = [r for r in repos if r.get("name") in space_repo_names]
            if space_repos:
                if len(space_repos) == 1:
                    return space_repos[0]["path"]
                repos = space_repos
                log.info("repo_narrowed_by_space", space_id=space_id, candidates=len(repos))

    if len(repos) == 1:
        return repos[0]["path"]

    # 1 & 2. Entity-based matching (exact then substring)
    repo_entities = [e for e in router_output.entities if e.entity_type in ("repo", "repository")]
    for entity in repo_entities:
        val = entity.value.lower().strip()
        # Exact name match first
        for repo in repos:
            if val == repo.get("name", "").lower():
                return repo["path"]
        # Substring match on name or remote_id
        for repo in repos:
            repo_name = repo.get("name", "").lower()
            remote_id = repo.get("remote_id", "").lower()
            if val and (val in repo_name or repo_name in val or val in remote_id or remote_id in val):
                return repo["path"]

    # 3. Keyword match: scan event text for repo names
    if event:
        search_text = f"{event.subject.title} {event.content.body[:500]}".lower()
        # Score each repo by how many of its name parts appear in the text
        best_repo = None
        best_score = 0
        for repo in repos:
            name = repo.get("name", "").lower()
            if not name:
                continue
            # Check full name first
            if name in search_text:
                return repo["path"]
            # Check name parts (e.g. "laya" from "laya-engine")
            parts = [p for p in name.replace("-", " ").replace("_", " ").split() if len(p) > 2]
            score = sum(1 for p in parts if p in search_text)
            if score > best_score:
                best_score = score
                best_repo = repo
        if best_repo and best_score > 0:
            return best_repo["path"]

    # 4. Fallback: first repo in the (possibly narrowed) candidate list
    return repos[0]["path"]


async def _gather_context(event: LayaEvent, router_output: RouterOutput) -> list[dict]:
    """Gather related context from ChromaDB memory."""
    query = f"{event.subject.title} {event.content.body[:300]}"
    try:
        return await memory_search(query, n_results=5)
    except Exception as e:
        log.warning("engineer_context_search_failed", error=str(e))
        return []


async def _build_agent_prompt(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict],
) -> str:
    """Call LLM to build a detailed task prompt for the coding agent."""
    messages = build_engineer_messages(event, router_output, related_context)
    schema = get_engineer_json_schema()

    response = await llm_call(
        role="stager",  # Use the strong model for prompt generation
        messages=messages,
        response_schema=schema,
        event_id=event.event_id,
        step="worker",
        temperature=0.2,
        max_tokens=8192,
    )

    if response.parsed:
        return response.parsed.get("task_prompt", response.content)
    return response.content


async def run_engineer(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
    space_id: str | None = None,
) -> WorkerResult:
    """Run the ENGINEER worker.

    Checks agent configuration and execution mode:
    - No agent configured → gather context + build prompt, skip agent spawn
    - Agent + automatic → spawn agent immediately
    - Agent + requires_approval → store prompt, defer agent spawn to user approval
    """
    log.info("engineer_worker_start", event_id=event.event_id)

    settings = load_settings()
    agent_type = settings.get("coding_agent", "none")
    exec_mode = settings.get("agent_execution_mode", "requires_approval")

    # 1. Gather context
    related_context = await _gather_context(event, router_output)

    # 2. Build agent prompt
    agent_prompt = await _build_agent_prompt(event, router_output, related_context)

    # --- No agent configured: skip agent, return findings with prompt ---
    if agent_type == "none":
        log.info("engineer_no_agent_configured", event_id=event.event_id)
        return WorkerResult(
            persona="ENGINEER",
            findings={"agent_prompt": agent_prompt, "agent_skipped": True},
            card_status="ready",
        )

    # 3. Resolve repo
    repo_path = await _resolve_repo_path(router_output, event, space_id=space_id)
    if not repo_path:
        log.warning("engineer_no_repo", event_id=event.event_id)
        return WorkerResult(
            persona="ENGINEER",
            findings={"agent_prompt": agent_prompt, "agent_skipped": True},
            card_status="ready",
            error="No repository configured. Add repos in Settings > Repos.",
        )

    # --- Requires approval: store prompt and defer ---
    if exec_mode == "requires_approval":
        log.info("engineer_deferred", event_id=event.event_id, card_id=card_id)
        effective_card_id = card_id or event.event_id
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET agent_prompt = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (agent_prompt, effective_card_id),
        )
        await db.commit()
        return WorkerResult(
            persona="ENGINEER",
            findings={"agent_prompt": agent_prompt, "deferred": True},
            card_status="requires_approval",
        )

    # --- Automatic mode: spawn agent immediately ---

    # 4. Spawn agent session
    effective_card_id = card_id or event.event_id
    try:
        session_id, agent = await session_manager.start_session(
            card_id=effective_card_id,
            prompt=agent_prompt,
            repo_path=repo_path,
            space_id=space_id,
        )
    except Exception as e:
        log.error("engineer_agent_spawn_failed", error=str(e))
        return WorkerResult(persona="ENGINEER", error=f"Failed to spawn coding agent: {e}")

    # 5. Stream events — persist all to SQLite, only broadcast approval/error via WS
    findings: dict[str, Any] = {}
    cc_session_id_stored = False

    try:
        async for ws_event in agent.stream_events():
            # Persist to SQLite; INSERT OR IGNORE skips duplicates from --resume replays
            inserted = await session_manager.store_workspace_event(ws_event)
            if not inserted:
                continue  # Duplicate from replayed history — skip all downstream logic

            # Persist cc_session_id once captured from system.init
            if not cc_session_id_stored and hasattr(agent, "cc_session_id") and agent.cc_session_id:
                await session_manager.store_cc_session_id(session_id, agent.cc_session_id)
                cc_session_id_stored = True

            # Broadcast approval requests (including AskUserQuestion) and errors
            if ws_event.event_type == WorkspaceEventType.APPROVAL_REQUEST:
                # If this is an AskUserQuestion, also update card status
                if ws_event.content.get("ask_user_question"):
                    db = await get_db()
                    await db.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (effective_card_id,),
                    )
                    await db.commit()
                    await manager.broadcast(
                        {
                            "type": "card_updated",
                            "card_id": effective_card_id,
                            "payload": {"status": "awaiting_input"},
                        }
                    )

                await manager.broadcast(
                    {
                        "type": "approval_request",
                        "card_id": effective_card_id,
                        "session_id": session_id,
                        "payload": ws_event.content,
                    }
                )
            elif ws_event.event_type == WorkspaceEventType.ERROR:
                findings["last_error"] = ws_event.content.get("error", "")
                await manager.broadcast(
                    {
                        "type": "agent_error",
                        "card_id": effective_card_id,
                        "session_id": session_id,
                        "payload": ws_event.content,
                    }
                )

            # Capture plan from ExitPlanMode
            if ws_event.event_type == WorkspaceEventType.AGENT_MESSAGE and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")

            # Collect result data
            if ws_event.event_type == WorkspaceEventType.STATUS_CHANGE:
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error("engineer_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        return WorkerResult(persona="ENGINEER", session_id=session_id, error=str(e))

    # 6. Complete session (unless agent is waiting for user input)
    final_status = agent.get_status()
    if final_status == SessionStatus.AWAITING_INPUT:
        # Agent asked a question — session stays open, worker returns partial result
        log.info("engineer_awaiting_input", session_id=session_id, card_id=effective_card_id)
        return WorkerResult(
            persona="ENGINEER",
            findings=findings,
            session_id=session_id,
        )

    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

        db = await get_db()

        # Write plan or agent result to card as staged_output (visible in CardDetail)
        # Prefer the plan (from ExitPlanMode) over the generic result summary
        agent_plan = findings.get("agent_plan", "")
        agent_result = findings.get("agent_result", "")
        staged_content = agent_plan or agent_result
        if staged_content:
            staged_type = "agent_plan" if agent_plan else "agent_result"
            await db.execute(
                "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (json.dumps({"type": staged_type, "content": staged_content}), effective_card_id),
            )

        # If there are unanswered questions, keep card as awaiting_input
        # so the notification persists until the user responds
        has_unanswered = await session_manager.has_unanswered_questions(session_id)
        card_status = "awaiting_input" if has_unanswered else "ready"

        await db.execute(
            "UPDATE action_cards SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_status, effective_card_id),
        )
        await db.commit()

        await manager.broadcast(
            {
                "type": "card_updated",
                "card_id": effective_card_id,
                "payload": {"status": card_status},
            }
        )
        await manager.broadcast(
            {
                "type": "agent_completed",
                "card_id": effective_card_id,
                "session_id": session_id,
                "payload": {"findings": findings},
            }
        )

        return WorkerResult(
            persona="ENGINEER",
            findings=findings,
            session_id=session_id,
        )
    elif final_status == SessionStatus.CANCELLED:
        await session_manager.complete_session(session_id, error="Cancelled by user")

        # Revert card from agent_running back to ready so user can retry
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (effective_card_id,),
        )
        await db.commit()

        await manager.broadcast(
            {
                "type": "card_updated",
                "card_id": effective_card_id,
                "payload": {"status": "ready"},
            }
        )
        await manager.broadcast(
            {
                "type": "agent_completed",
                "card_id": effective_card_id,
                "session_id": session_id,
                "payload": {"status": "cancelled"},
            }
        )

        return WorkerResult(
            persona="ENGINEER",
            session_id=session_id,
            error="Cancelled by user",
        )
    else:
        last_error = findings.get("last_error", "")
        error_msg = f"Agent ended with status: {final_status.value}"
        if last_error:
            error_msg += f" — {last_error}"
        log.error("engineer_agent_failed", session_id=session_id, error=error_msg)
        await session_manager.complete_session(session_id, error=error_msg)

        # Mark card as failed so it doesn't stay stuck on agent_running
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (effective_card_id,),
        )
        await db.commit()

        await manager.broadcast(
            {
                "type": "card_updated",
                "card_id": effective_card_id,
                "payload": {"status": "failed"},
            }
        )

        return WorkerResult(persona="ENGINEER", session_id=session_id, error=error_msg)


async def run_engineer_from_prompt(
    card_id: str,
    agent_prompt: str,
    space_id: str | None = None,
) -> None:
    """Spawn a coding agent for a card that was previously deferred (requires_approval).

    Called as a background task from the approve-agent endpoint.  Resolves
    the repo, spawns the agent, streams events, and updates card status
    exactly like the automatic path in run_engineer().
    """
    log.info("engineer_from_prompt_start", card_id=card_id)

    # Resolve repo — we need a RouterOutput-like object, but we only need entities.
    # Since the prompt is already built, we just need the repo path.
    from laya.models.classification import RouterOutput, Persona, Priority, Category

    # Build a minimal RouterOutput for repo resolution
    dummy_router = RouterOutput(
        persona=Persona.ENGINEER,
        priority=Priority.MEDIUM,
        category=Category.CODE_REVIEW,
        confidence=0.8,
        entities=[],
    )
    repo_path = await _resolve_repo_path(dummy_router, space_id=space_id)
    if not repo_path:
        log.error("engineer_from_prompt_no_repo", card_id=card_id)
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Spawn agent
    try:
        session_id, agent = await session_manager.start_session(
            card_id=card_id,
            prompt=agent_prompt,
            repo_path=repo_path,
            space_id=space_id,
        )
    except Exception as e:
        log.error("engineer_from_prompt_spawn_failed", card_id=card_id, error=str(e))
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Stream events (same logic as run_engineer's main loop)
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

            if ws_event.event_type == WorkspaceEventType.APPROVAL_REQUEST:
                if ws_event.content.get("ask_user_question"):
                    db = await get_db()
                    await db.execute(
                        "UPDATE action_cards SET status = 'awaiting_input', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                        (card_id,),
                    )
                    await db.commit()
                    await manager.broadcast(
                        {"type": "card_updated", "card_id": card_id, "payload": {"status": "awaiting_input"}}
                    )
                await manager.broadcast(
                    {"type": "approval_request", "card_id": card_id, "session_id": session_id, "payload": ws_event.content}
                )
            elif ws_event.event_type == WorkspaceEventType.ERROR:
                findings["last_error"] = ws_event.content.get("error", "")
                await manager.broadcast(
                    {"type": "agent_error", "card_id": card_id, "session_id": session_id, "payload": ws_event.content}
                )

            if ws_event.event_type == WorkspaceEventType.AGENT_MESSAGE and ws_event.content.get("is_plan"):
                findings["agent_plan"] = ws_event.content.get("text", "")
            if ws_event.event_type == WorkspaceEventType.STATUS_CHANGE:
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error("engineer_from_prompt_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        db = await get_db()
        await db.execute(
            "UPDATE action_cards SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
        return

    # Complete session and update card status
    final_status = agent.get_status()
    db = await get_db()

    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

        agent_plan = findings.get("agent_plan", "")
        agent_result = findings.get("agent_result", "")
        staged_content = agent_plan or agent_result
        if staged_content:
            staged_type = "agent_plan" if agent_plan else "agent_result"
            await db.execute(
                "UPDATE action_cards SET staged_output = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                (json.dumps({"type": staged_type, "content": staged_content}), card_id),
            )

        has_unanswered = await session_manager.has_unanswered_questions(session_id)
        card_status = "awaiting_input" if has_unanswered else "ready"

        await db.execute(
            "UPDATE action_cards SET status = ?, has_workspace = 1, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_status, card_id),
        )
        await db.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": card_status, "has_workspace": True}}
        )
        await manager.broadcast(
            {"type": "agent_completed", "card_id": card_id, "session_id": session_id, "payload": {"findings": findings}}
        )
    else:
        last_error = findings.get("last_error", "")
        error_msg = f"Agent ended with status: {final_status.value}"
        if last_error:
            error_msg += f" — {last_error}"
        log.error("engineer_from_prompt_failed", session_id=session_id, error=error_msg)
        await session_manager.complete_session(session_id, error=error_msg)
        await db.execute(
            "UPDATE action_cards SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
            (card_id,),
        )
        await db.commit()
        await manager.broadcast(
            {"type": "card_updated", "card_id": card_id, "payload": {"status": "failed"}}
        )
