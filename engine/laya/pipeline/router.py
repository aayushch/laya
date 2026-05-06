"""ROUTER pipeline step — LLM classification, entity extraction, memory search."""

import json
import uuid

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.router import (
    build_batch_router_messages,
    build_router_messages,
    get_batch_router_json_schema,
    get_router_json_schema,
)
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

log = structlog.get_logger()


async def _query_related_context(event: LayaEvent) -> list[dict]:
    """Search ChromaDB for past events related to this one."""
    query_parts = [event.subject.title, event.content.body[:300]]
    query = " ".join(query_parts)

    try:
        results = await memory_search(query, n_results=3)
        log.debug("related_context_found", count=len(results), event_id=event.event_id)
        return results
    except Exception as e:
        log.warning("memory_search_skipped", error=str(e), event_id=event.event_id)
        return []


async def _store_entities(event_id: str, router_output: RouterOutput) -> None:
    """Store extracted entities in the entities table (Layer 1: explicit)."""
    if not router_output.entities:
        return

    db = await get_db()

    for entity in router_output.entities:
        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
        platform = entity.platform or "unknown"
        platform_refs = json.dumps({platform: [entity.value]})

        # Check for existing entity with same canonical name
        async with db.execute(
            "SELECT entity_id, platform_refs FROM entities WHERE canonical_name = ?",
            (entity.value,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            # Merge platform refs into existing entity
            existing_refs = json.loads(existing[1])
            if platform not in existing_refs:
                existing_refs[platform] = []
            if entity.value not in existing_refs[platform]:
                existing_refs[platform].append(entity.value)

            await db.execute(
                "UPDATE entities SET platform_refs = ?, updated_at = CURRENT_TIMESTAMP WHERE entity_id = ?",
                (json.dumps(existing_refs), existing[0]),
            )
        else:
            await db.execute(
                """INSERT INTO entities
                   (entity_id, entity_type, canonical_name, platform_refs, link_method, confidence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entity_id, entity.entity_type, entity.value, platform_refs, "explicit", 1.0),
            )

    await db.commit()
    log.debug("entities_stored", count=len(router_output.entities), event_id=event_id)



async def run_router(
    event: LayaEvent, actor_relationship: str, space_id: str | None = None
) -> RouterOutput:
    """Run the ROUTER step: classify event, extract entities, embed in memory.

    Args:
        event: The event to classify.
        actor_relationship: Resolved from team.json ("manager", "teammate", etc.).
        space_id: Optional space for model/key overrides.

    Returns:
        RouterOutput with classification, entities, research plan.
    """
    # 1. Query ChromaDB for related past events
    related_context = await _query_related_context(event)

    # 1b. Query user feedback patterns + classification rules/corrections for learning loop
    from laya.pipeline.feedback import (
        format_feedback_section,
        query_classification_corrections,
        query_classification_rules,
        query_feedback_patterns,
    )

    feedback_patterns = await query_feedback_patterns(event)
    cls_rules = await query_classification_rules(space_id)
    cls_corrections = await query_classification_corrections(event.source.platform)
    feedback_section = format_feedback_section(feedback_patterns, cls_rules, cls_corrections)

    # 2. Build prompt and call LLM
    messages = build_router_messages(
        event, actor_relationship, related_context, feedback_context=feedback_section
    )
    schema = get_router_json_schema()

    response = await llm_call(
        role="router",
        messages=messages,
        response_schema=schema,
        event_id=event.event_id,
        step="route",
        temperature=0.0,
        max_tokens=1500,
        space_id=space_id,
    )

    # 3. Parse and validate the response
    if response.parsed:
        router_output = RouterOutput(**response.parsed)
    else:
        # Fallback: try to parse the raw content as JSON
        try:
            parsed = json.loads(response.content)
            router_output = RouterOutput(**parsed)
        except (json.JSONDecodeError, Exception) as e:
            log.error("router_parse_failed", event_id=event.event_id, error=str(e))
            # Return a safe default classification
            router_output = RouterOutput(
                category="OPS",
                persona="OPS",
                priority="MEDIUM",
                confidence=0.0,
                reasoning=f"Parse error, defaulting: {e}",
            )

    # 4. Store router output in events table
    db = await get_db()
    await db.execute(
        "UPDATE events SET router_output = ?, processed = TRUE WHERE event_id = ?",
        (router_output.model_dump_json(), event.event_id),
    )
    await db.commit()

    # 5. Store extracted entities
    await _store_entities(event.event_id, router_output)

    log.info(
        "router_complete",
        event_id=event.event_id,
        category=router_output.category.value,
        persona=router_output.persona.value,
        priority=router_output.priority.value,
        confidence=router_output.confidence,
        entity_count=len(router_output.entities),
        requires_research=router_output.requires_research,
    )

    return router_output


async def run_batch_router(
    events_data: list[dict],
) -> dict[str, RouterOutput]:
    """Classify multiple events in a single LLM call for efficiency.

    Falls back to individual routing on parse failure for any event.

    Args:
        events_data: List of dicts with keys: event_id, event, actor_relationship, space_id

    Returns:
        Dict mapping event_id → RouterOutput
    """
    if len(events_data) == 1:
        item = events_data[0]
        output = await run_router(
            item["event"], item["actor_relationship"], space_id=item["space_id"]
        )
        return {item["event_id"]: output}

    messages = build_batch_router_messages(events_data)
    schema = get_batch_router_json_schema(len(events_data))

    space_id = events_data[0].get("space_id")

    response = await llm_call(
        role="router",
        messages=messages,
        response_schema=schema,
        step="route_batch",
        temperature=0.0,
        max_tokens=1500 * len(events_data),
        space_id=space_id,
    )

    results: dict[str, RouterOutput] = {}

    if response.parsed and "classifications" in response.parsed:
        for i, classification in enumerate(response.parsed["classifications"]):
            if i >= len(events_data):
                break
            event_id = events_data[i]["event_id"]
            try:
                router_output = RouterOutput(**classification)
                results[event_id] = router_output

                # Store router output in DB
                db = await get_db()
                await db.execute(
                    "UPDATE events SET router_output = ?, processed = TRUE WHERE event_id = ?",
                    (router_output.model_dump_json(), event_id),
                )

                # Store entities
                await _store_entities(event_id, router_output)

                log.info(
                    "batch_router_classified",
                    event_id=event_id,
                    category=router_output.category.value,
                    persona=router_output.persona.value,
                    priority=router_output.priority.value,
                    batch_index=i,
                )
            except Exception as e:
                log.warning("batch_router_parse_single", event_id=event_id, error=str(e))

        # Commit all DB updates
        db = await get_db()
        await db.commit()

    log.info(
        "batch_router_complete",
        total_events=len(events_data),
        classified=len(results),
    )

    return results
