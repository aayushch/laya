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
from laya.pipeline.related_context import query_related_context
from laya.db.sqlite import get_db
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
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


# Metadata keys the ingestion workflows use to name the repo an event belongs
# to. GitHub writes "repo" as "owner/repo" (matches a repo's remote_id);
# Bitbucket writes "bb_repository" as the bare slug.
_REPO_METADATA_KEYS = ("repo", "bb_repository", "repository")


def _repo_refs_from_event(event: LayaEvent | None) -> list[str]:
    """Repo names the platform itself attached to the event's metadata."""
    if event is None:
        return []
    refs = []
    for key in _REPO_METADATA_KEYS:
        val = event.content.metadata.get(key)
        if isinstance(val, str) and val.strip():
            refs.append(val)
    return refs


def _repo_refs_from_router(router_output: RouterOutput | None) -> list[str]:
    """Repo names the router extracted as entities."""
    if router_output is None:
        return []
    return [
        e.value for e in router_output.entities
        if e.entity_type in ("repo", "repository") and e.value
    ]


def _search_text_from_event(event: LayaEvent | None) -> str:
    """Event title and body excerpt scanned for repo names (keyword step)."""
    if event is None:
        return ""
    return f"{event.subject.title} {event.content.body[:500]}".lower()


def _dedupe_refs(refs: list[str]) -> list[str]:
    """Drop duplicate refs case-insensitively, keeping first occurrence order."""
    seen: set[str] = set()
    out = []
    for ref in refs:
        key = ref.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(ref)
    return out


async def _match_repo(
    repo_refs: list[str],
    search_text: str,
    space_id: str | None,
) -> tuple[str | None, list[str]]:
    """Pick the agent's primary repo from the registered repos.

    Args:
        repo_refs: Candidate repo names/slugs, strongest first (platform metadata,
            then router entities).
        search_text: Lower-cased free text (event titles/bodies) scanned for repo
            names when no ref matches.
        space_id: Narrows candidates to the space's assigned repos.

    Returns:
        Tuple of (primary_repo_path, additional_dir_paths). The additional dirs are
        always the remaining candidate repos, so the agent keeps full context even
        when resolution had to fall back.

    Matching strategy (first match wins):
    0. If space has repos assigned, narrow candidates to those repos.
       If only one candidate remains, return it immediately.
    1. Exact match of a ref vs repo name
    2. Substring match of a ref vs repo name or remote_id
    3. Keyword match: scan search_text for repo names / remote_ids
    4. Fallback: first repo in the (possibly narrowed) candidate list
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

    # 1 & 2. Ref-based matching (exact then substring) — confident resolution
    for ref in repo_refs:
        val = ref.lower().strip()
        if not val:
            continue
        # Exact name match first
        for repo in repos:
            if val == repo.get("name", "").lower():
                path = repo["path"]
                log.info("repo_resolved", method="ref_exact", ref=ref, primary=path)
                return path, _other_paths(path)
        # Substring match on name or remote_id
        for repo in repos:
            repo_name = repo.get("name", "").lower()
            remote_id = repo.get("remote_id", "").lower()
            if (repo_name and (val in repo_name or repo_name in val)) or (
                remote_id and (val in remote_id or remote_id in val)
            ):
                path = repo["path"]
                log.info("repo_resolved", method="ref_substring", ref=ref, primary=path)
                return path, _other_paths(path)

    # 3. Keyword match: scan free text for repo names — confident resolution
    if search_text:
        best_repo = None
        best_score = 0
        for repo in repos:
            name = repo.get("name", "").lower()
            remote_id = repo.get("remote_id", "").lower()
            if not name:
                continue
            if name in search_text or (remote_id and remote_id in search_text):
                path = repo["path"]
                log.info("repo_resolved", method="keyword", primary=path)
                return path, _other_paths(path)
            parts = [p for p in name.replace("-", " ").replace("_", " ").split() if len(p) > 2]
            score = sum(1 for p in parts if p in search_text)
            if score > best_score:
                best_score = score
                best_repo = repo
        if best_repo and best_score > 0:
            path = best_repo["path"]
            log.info("repo_resolved", method="keyword_partial", primary=path, score=best_score)
            return path, _other_paths(path)

    # 4. Fallback: first repo + ALL remaining repos as additional dirs
    # (repo resolution failed, give the agent access to everything)
    primary = repos[0]["path"]
    log.info("repo_resolution_fallback", primary=primary, add_dirs=len(all_repo_paths) - 1)
    return primary, _other_paths(primary)


async def resolve_repo_path(
    router_output: RouterOutput,
    event: LayaEvent | None = None,
    space_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Resolve the target repo for a single classified event.

    Signals, strongest first: the repo named in the event's platform metadata,
    then the router's repo entities, then a keyword scan of the event text.
    See ``_match_repo`` for the matching order and the return contract.
    """
    repo_refs = _dedupe_refs(_repo_refs_from_event(event) + _repo_refs_from_router(router_output))
    return await _match_repo(repo_refs, _search_text_from_event(event), space_id)


async def resolve_entity_repo_path(
    entity_id: str,
    space_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Resolve the target repo for an entity-level agent run.

    An entity group spans several cards, each with its own event. Every event's
    persisted classification (``events.router_output``) and payload
    (``events.raw_json``) contributes signals, newest card first, so a repo named
    on any card in the group is found. Rows whose JSON is missing or unparsable
    (a reclassify clears router_output until the router re-runs) are skipped.
    See ``_match_repo`` for the matching order and the return contract.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT e.raw_json, e.router_output
           FROM action_cards ac
           JOIN events e ON e.event_id = ac.event_id
           WHERE ac.entity_id = ?
           ORDER BY ac.created_at DESC""",
        (entity_id,),
    )

    repo_refs: list[str] = []
    # The entity id itself ("platform:subject_type:subject_id") is scanned too, so
    # a subject id that embeds the repo slug matches without any event payload.
    text_parts: list[str] = [entity_id.lower()]
    for row in rows:
        event = None
        if row["raw_json"]:
            try:
                event = LayaEvent.model_validate_json(row["raw_json"])
            except Exception:
                event = None
        router_output = None
        if row["router_output"]:
            try:
                router_output = RouterOutput.model_validate_json(row["router_output"])
            except Exception:
                router_output = None
        repo_refs.extend(_repo_refs_from_event(event))
        repo_refs.extend(_repo_refs_from_router(router_output))
        text = _search_text_from_event(event)
        if text:
            text_parts.append(text)

    repo_refs = _dedupe_refs(repo_refs)
    log.debug(
        "entity_repo_signals", entity_id=entity_id, events=len(rows), refs=repo_refs,
    )
    return await _match_repo(repo_refs, " ".join(text_parts), space_id)


async def _gather_context(event: LayaEvent, router_output: RouterOutput) -> list[dict]:
    """Gather related context from ChromaDB memory (shared per-event — P6-7)."""
    return await query_related_context(event, n_results=5)


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
        max_tokens=DEFAULT_MAX_TOKENS,
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
