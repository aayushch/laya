"""STAGER pipeline step — synthesize worker findings into action card data."""

import json

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.stager import build_stager_messages, get_stager_json_schema
from laya.models.card import ActionCardData, StagedOutput, SuggestedAction
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def _query_entity_history(event: LayaEvent) -> list[dict]:
    """Fetch existing cards for this entity to give the stager context about
    what the user has already seen, preventing redundant research."""
    entity_id = f"{event.source.platform}:{event.subject.type}:{event.subject.id}"
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.header, ac.summary, ac.status, ac.created_at,
                  e.source_raw_event_type
           FROM action_cards ac
           JOIN events e ON e.event_id = ac.event_id
           WHERE ac.entity_id = ?
           ORDER BY ac.created_at DESC
           LIMIT 5""",
        (entity_id,),
    )
    return [dict(r) for r in rows]


async def run_stager(
    event: LayaEvent,
    router_output: RouterOutput,
    worker_results: list[WorkerResult] | None = None,
    space_id: str | None = None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
) -> ActionCardData:
    """Run the STAGER step: synthesize findings into a polished action card.

    Args:
        event: The original event.
        router_output: Router classification and entities.
        worker_results: Optional findings from workers (None for simple events).
        space_id: Optional space for model/key overrides.

    Returns:
        ActionCardData ready for the EMIT step.
    """
    # 1. Query ChromaDB for related context
    related_context = await _query_related_context(event)

    # 1b. Query existing cards for this entity (prevents redundant research)
    entity_history = await _query_entity_history(event)

    # 2. Build messages and call LLM
    messages = build_stager_messages(
        event, router_output, worker_results, related_context, entity_history,
        user_identity=user_identity, actor_relationship=actor_relationship,
        participant_roles=participant_roles,
    )
    schema = get_stager_json_schema()

    try:
        response = await llm_call(
            role="stager",
            messages=messages,
            response_schema=schema,
            event_id=event.event_id,
            step="stage",
            temperature=0.2,
            max_tokens=2000,
            space_id=space_id,
        )
    except Exception as e:
        log.error("stager_llm_failed", event_id=event.event_id, error=str(e))
        return _build_fallback_card(event, router_output)

    # 3. Parse response into ActionCardData
    if response.parsed:
        return _parse_stager_response(response.parsed, event)

    # Fallback: try raw content
    try:
        parsed = json.loads(response.content)
        return _parse_stager_response(parsed, event)
    except (json.JSONDecodeError, Exception) as e:
        log.error("stager_parse_failed", event_id=event.event_id, error=str(e))
        return _build_fallback_card(event, router_output)


async def _query_related_context(event: LayaEvent) -> list[dict]:
    """Search ChromaDB for related content to enrich stager context."""
    query_parts = [event.subject.title, event.content.body[:300]]
    query = " ".join(query_parts)

    try:
        results = await memory_search(query, n_results=3)
        log.debug("stager_context_found", count=len(results), event_id=event.event_id)
        return results
    except Exception as e:
        log.warning("stager_memory_search_skipped", error=str(e), event_id=event.event_id)
        return []


_CROSS_PLATFORM_ALLOWED: dict[tuple[str, str], bool] = {
    # (event_platform, target_platform) combinations accepted as legitimate.
    # Same-platform pairs are always allowed; this map covers exceptions only.
    ("gmail", "outlook"): True,
    ("outlook", "gmail"): True,
}


def _is_platform_compatible(event_platform: str, target_platform: str) -> bool:
    """Suggested actions should target the event's own platform. A tiny
    allowlist permits mail-to-mail forwarding; everything else is rejected
    so the LLM cannot suggest unrelated platforms (e.g. google_calendar on
    an outlook payment confirmation)."""
    if not target_platform:
        return False
    if event_platform == target_platform:
        return True
    return _CROSS_PLATFORM_ALLOWED.get((event_platform, target_platform), False)


def _parse_stager_response(data: dict, event: LayaEvent) -> ActionCardData:
    """Parse the LLM response dict into ActionCardData."""
    event_platform = event.source.platform
    actions = []
    dropped: list[dict] = []
    for act in data.get("suggested_actions", []):
        payload = act.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}

        target_platform = act.get("target_platform", "")
        if not _is_platform_compatible(event_platform, target_platform):
            dropped.append({
                "action_id": act.get("action_id"),
                "target_platform": target_platform,
                "action_type": act.get("action_type"),
            })
            continue

        actions.append(
            SuggestedAction(
                action_id=act["action_id"],
                label=act["label"],
                action_type=act["action_type"],
                target_platform=target_platform,
                payload=payload,
            )
        )

    if dropped:
        log.info(
            "stager_dropped_cross_platform_actions",
            event_id=event.event_id,
            event_platform=event_platform,
            dropped=dropped,
        )

    staged = data.get("staged_output", {})
    return ActionCardData(
        header=data.get("header", event.subject.title)[:80],
        summary=data.get("summary", ""),
        intelligence_report=data.get("intelligence_report", []),
        staged_output=StagedOutput(
            type=staged.get("type", "summary"),
            content=staged.get("content", ""),
        ),
        suggested_actions=actions,
        privacy_tier=max(1, min(3, int(data.get("privacy_tier", 2)))),
    )


def _build_fallback_card(event: LayaEvent, router_output: RouterOutput) -> ActionCardData:
    """Build a minimal card when the stager LLM fails."""
    return ActionCardData(
        header=f"Review: {event.subject.title}"[:80],
        summary=(
            f"New {router_output.category.value.lower()} event from "
            f"{event.source.platform}: {event.subject.title}"
        ),
        intelligence_report=[
            f"Source: {event.source.platform} ({event.source.raw_event_type})",
            f"Actor: {event.actor.name}",
            f"Priority: {router_output.priority.value}",
        ],
        staged_output=StagedOutput(type="summary", content=event.content.body[:500]),
        suggested_actions=[],
        privacy_tier=2,
    )
