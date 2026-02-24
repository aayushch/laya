"""ENGINEER Worker — coding agent orchestration for code research and fixes."""

from __future__ import annotations

from typing import Any

import structlog

from laya.agents import session_manager
from laya.api.websocket import manager
from laya.config import load_repos
from laya.db.chromadb_store import memory_search
from laya.llm.client import llm_call
from laya.llm.prompts.engineer import build_engineer_messages, get_engineer_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.models.workspace import SessionStatus, WorkspaceEventType
from laya.workers.base import WorkerResult

log = structlog.get_logger()


def _resolve_repo_path(router_output: RouterOutput) -> str | None:
    """Resolve the target repo path from entities + repos.json."""
    repos_data = load_repos()
    repos = repos_data.get("repos", [])

    if not repos:
        return None

    # Try to match an entity to a known repo
    for entity in router_output.entities:
        if entity.entity_type in ("repo", "repository"):
            for repo in repos:
                if entity.value.lower() in repo.get("name", "").lower():
                    return repo["path"]
                if entity.value.lower() in repo.get("remote_id", "").lower():
                    return repo["path"]

    # Fallback: use the first configured repo
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
        max_tokens=2000,
    )

    if response.parsed:
        return response.parsed.get("task_prompt", response.content)
    return response.content


async def run_engineer(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
) -> WorkerResult:
    """Run the ENGINEER worker.

    1. Gather context from memory
    2. Build agent task prompt via LLM
    3. Resolve target repo
    4. Spawn coding agent session
    5. Stream events to WebSocket + persist to SQLite
    6. Return structured findings
    """
    log.info("engineer_worker_start", event_id=event.event_id)

    # 1. Gather context
    related_context = await _gather_context(event, router_output)

    # 2. Build agent prompt
    agent_prompt = await _build_agent_prompt(event, router_output, related_context)

    # 3. Resolve repo
    repo_path = _resolve_repo_path(router_output)
    if not repo_path:
        log.warning("engineer_no_repo", event_id=event.event_id)
        return WorkerResult(
            persona="ENGINEER",
            error="No repository configured. Add repos in Settings > Repos.",
        )

    # 4. Spawn agent session
    effective_card_id = card_id or event.event_id
    try:
        session_id, agent = await session_manager.start_session(
            card_id=effective_card_id,
            prompt=agent_prompt,
            repo_path=repo_path,
        )
    except Exception as e:
        log.error("engineer_agent_spawn_failed", error=str(e))
        return WorkerResult(persona="ENGINEER", error=f"Failed to spawn coding agent: {e}")

    # 5. Stream events
    findings: dict[str, Any] = {}
    try:
        async for ws_event in agent.stream_events():
            # Persist to SQLite
            await session_manager.store_workspace_event(ws_event)

            # Broadcast to WebSocket
            await manager.broadcast(
                {
                    "type": "agent_progress",
                    "card_id": effective_card_id,
                    "session_id": session_id,
                    "payload": {
                        "event_type": ws_event.event_type.value,
                        "content": ws_event.content,
                        "requires_input": ws_event.requires_input,
                    },
                }
            )

            # If approval request, also broadcast the specific message type
            if ws_event.event_type == WorkspaceEventType.APPROVAL_REQUEST:
                await manager.broadcast(
                    {
                        "type": "approval_request",
                        "card_id": effective_card_id,
                        "session_id": session_id,
                        "payload": ws_event.content,
                    }
                )

            # Collect result data
            if ws_event.event_type == WorkspaceEventType.STATUS_CHANGE:
                if ws_event.content.get("status") == "result_received":
                    findings["agent_result"] = ws_event.content.get("result", "")

    except Exception as e:
        log.error("engineer_stream_error", session_id=session_id, error=str(e))
        await session_manager.complete_session(session_id, error=str(e))
        return WorkerResult(persona="ENGINEER", session_id=session_id, error=str(e))

    # 6. Complete session
    final_status = agent.get_status()
    if final_status == SessionStatus.COMPLETED:
        await session_manager.complete_session(session_id, findings=findings)

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
    else:
        error_msg = f"Agent ended with status: {final_status.value}"
        await session_manager.complete_session(session_id, error=error_msg)
        return WorkerResult(persona="ENGINEER", session_id=session_id, error=error_msg)
