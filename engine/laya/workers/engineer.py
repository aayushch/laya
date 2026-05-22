# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""ENGINEER Worker — builds coding agent task prompts for entity-level agent runs.

The engineer worker no longer spawns agents directly. It builds a detailed
task prompt from the event context and stores it on the card. Users invoke
agents at the entity/group level via the "Run Agent" flow.
"""

from __future__ import annotations

from typing import Any

import structlog

from laya.config import load_repos, load_settings
from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.engineer import build_engineer_messages, get_engineer_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
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


async def resolve_repo_path(
    router_output: RouterOutput,
    event: LayaEvent | None = None,
    space_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Resolve the target repo path and additional directories.

    Returns:
        Tuple of (primary_repo_path, additional_dir_paths).
        When repo resolution is confident (exact/entity/keyword match), additional dirs
        are the remaining repos. When resolution falls back to the first repo, ALL repos
        are included as additional dirs so the agent has full context.

    Matching strategy (first match wins):
    0. If space has repos assigned, narrow candidates to those repos.
       If only one repo is assigned to the space, return it immediately.
    1. Exact name match on entity value vs repo name
    2. Substring match on entity value vs repo name or remote_id
    3. Keyword match: scan event subject/body for repo names
    4. Fallback: first repo in the (possibly narrowed) candidate list + all others as add_dirs
    """
    repos_data = load_repos()
    repos = repos_data.get("repos", [])

    if not repos:
        return None, []

    all_repo_paths = [r["path"] for r in repos if r.get("path")]

    def _other_paths(chosen_path: str) -> list[str]:
        """Return all repo paths except the chosen one."""
        return [p for p in all_repo_paths if p != chosen_path]

    # 0. Narrow to space-assigned repos if available
    if space_id:
        space_repo_names = await _get_space_repos(space_id)
        if space_repo_names:
            space_repos = [r for r in repos if r.get("name") in space_repo_names]
            if space_repos:
                all_repo_paths = [r["path"] for r in space_repos if r.get("path")]
                if len(space_repos) == 1:
                    return space_repos[0]["path"], []
                repos = space_repos
                log.info("repo_narrowed_by_space", space_id=space_id, candidates=len(repos))

    if len(repos) == 1:
        return repos[0]["path"], []

    # 1 & 2. Entity-based matching (exact then substring) — confident resolution
    repo_entities = [e for e in router_output.entities if e.entity_type in ("repo", "repository")]
    for entity in repo_entities:
        val = entity.value.lower().strip()
        # Exact name match first
        for repo in repos:
            if val == repo.get("name", "").lower():
                path = repo["path"]
                return path, _other_paths(path)
        # Substring match on name or remote_id
        for repo in repos:
            repo_name = repo.get("name", "").lower()
            remote_id = repo.get("remote_id", "").lower()
            if val and (val in repo_name or repo_name in val or val in remote_id or remote_id in val):
                path = repo["path"]
                return path, _other_paths(path)

    # 3. Keyword match: scan event text for repo names — confident resolution
    if event:
        search_text = f"{event.subject.title} {event.content.body[:500]}".lower()
        best_repo = None
        best_score = 0
        for repo in repos:
            name = repo.get("name", "").lower()
            if not name:
                continue
            if name in search_text:
                path = repo["path"]
                return path, _other_paths(path)
            parts = [p for p in name.replace("-", " ").replace("_", " ").split() if len(p) > 2]
            score = sum(1 for p in parts if p in search_text)
            if score > best_score:
                best_score = score
                best_repo = repo
        if best_repo and best_score > 0:
            path = best_repo["path"]
            return path, _other_paths(path)

    # 4. Fallback: first repo + ALL remaining repos as additional dirs
    # (repo resolution failed, give the agent access to everything)
    primary = repos[0]["path"]
    log.info("repo_resolution_fallback", primary=primary, add_dirs=len(all_repo_paths) - 1)
    return primary, _other_paths(primary)


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

    Builds a detailed task prompt for a coding agent and stores it on
    the card. The agent is NOT spawned here — users invoke agents at
    the entity/group level via the "Run Agent" flow.
    """
    log.info("engineer_worker_start", event_id=event.event_id)

    related_context = await _gather_context(event, router_output)
    agent_prompt = await _build_agent_prompt(event, router_output, related_context)

    # Store agent_prompt on card for later use by entity-level "Run Agent"
    effective_card_id = card_id or event.event_id
    db = await get_db()
    await db.execute(
        "UPDATE action_cards SET agent_prompt = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (agent_prompt, effective_card_id),
    )
    await db.commit()

    log.info("engineer_prompt_stored", event_id=event.event_id, card_id=effective_card_id)
    return WorkerResult(
        persona="ENGINEER",
        findings={"agent_prompt": agent_prompt},
        card_status="ready",
    )
