# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Entity Resolution Layers 2 & 3 — semantic matching + LLM confirmation."""

import json
import uuid

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call

log = structlog.get_logger()

# Cosine distance threshold for semantic matching (lower = more similar).
# Configurable via settings.json tuning.semantic_entity_threshold
def _get_semantic_threshold() -> float:
    from laya.config import get_tuning
    return get_tuning("semantic_entity_threshold", 0.35)


async def resolve_semantic_entities(
    card_id: str,
    card_text: str,
    entities: list[str],
) -> list[dict]:
    """Layer 2: Semantic matching via ChromaDB.

    For each entity value, searches ChromaDB for semantically similar content
    and links entities that appear related.

    Non-critical — failures are logged but never block emit.

    Args:
        card_id: The card these entities belong to.
        card_text: Combined header + summary for context.
        entities: List of entity values to resolve.

    Returns:
        List of matched entity dicts with link info.
    """
    matched = []

    for entity_name in entities:
        try:
            results = await memory_search(entity_name, n_results=5)

            for result in results:
                distance = result.get("distance", 1.0)
                if distance > _get_semantic_threshold():
                    continue

                metadata = result.get("metadata", {})
                entity_refs = metadata.get("entity_refs", "")

                # Check if the result references a different entity (cross-reference)
                if entity_refs and entity_name not in entity_refs:
                    ref_entities = [r.strip() for r in entity_refs.split(",") if r.strip()]
                    for ref in ref_entities:
                        link = await _create_semantic_link(
                            entity_name, ref, distance, card_id
                        )
                        if link:
                            matched.append(link)

        except Exception as e:
            log.warning(
                "semantic_resolution_failed",
                entity=entity_name,
                card_id=card_id,
                error=str(e),
            )

    log.debug("semantic_resolution_done", card_id=card_id, matches=len(matched))
    return matched


async def _create_semantic_link(
    entity_a: str,
    entity_b: str,
    distance: float,
    card_id: str,
) -> dict | None:
    """Create a semantic entity link in the database."""
    db = await get_db()
    confidence = 1.0 - distance

    # Check if entity_b exists in entities table
    async with db.execute(
        "SELECT entity_id FROM entities WHERE canonical_name = ?",
        (entity_b,),
    ) as cursor:
        existing = await cursor.fetchone()

    if not existing:
        return None

    # Insert a new entity row linking entity_a with semantic method
    entity_id = f"ent_{uuid.uuid4().hex[:12]}"
    try:
        await db.execute(
            """INSERT OR IGNORE INTO entities
               (entity_id, entity_type, canonical_name, platform_refs,
                link_method, confidence)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entity_id,
                "cross_reference",
                f"{entity_a} <-> {entity_b}",
                json.dumps({"linked_from": card_id}),
                "semantic",
                confidence,
            ),
        )
        await db.commit()

        log.debug(
            "semantic_link_created",
            entity_a=entity_a,
            entity_b=entity_b,
            confidence=confidence,
        )

        return {
            "entity_id": entity_id,
            "entity_a": entity_a,
            "entity_b": entity_b,
            "link_method": "semantic",
            "confidence": confidence,
        }
    except Exception as e:
        log.warning("semantic_link_insert_failed", error=str(e))
        return None


# NOTE: the former Layer-3 `confirm_entity_link` LLM confirmation was removed as
# dead code — it was never called anywhere in the pipeline while still carrying a
# maintained prompt (review §5.7 / §2 entity dedup).


_CONTEXT_SYSTEM_PROMPTS = {
    "strict": (
        "You determine whether two notifications reference the EXACT SAME specific "
        "incident, transaction, ticket, or entity. They must share a concrete "
        "identifier — a ticket number, PR number, order ID, service name, or "
        "specific person + situation.\n\n"
        "Return match: true ONLY if you are highly confident both notifications "
        "point to a single real-world event or object.\n\n"
        "Two notifications about the same TYPE of issue (e.g. two different NPE "
        "bugs, two different payment errors, two security alerts for different "
        "services) are NOT the same issue. Two notifications that share vocabulary "
        "or domain but reference different specific entities are NOT a match.\n\n"
        "When in doubt, return match: false."
    ),
    "balanced": (
        "You determine whether two notifications are about the same "
        "real-world context or topic. Two notifications are about the "
        "same context if they refer to the same specific underlying "
        "entity, transaction, project, or situation — even if they come "
        "from different senders or platforms.\n\n"
        "Examples of SAME context:\n"
        "- A utility bill notification + a payment receipt for that bill\n"
        "- A PR comment + the CI build it triggered\n"
        "- A meeting invite + follow-up notes for that meeting\n"
        "- A shipping confirmation + delivery notification for the same order\n\n"
        "Examples of NOT same context:\n"
        "- Two different newsletters from different senders\n"
        "- Two unrelated promotional emails\n"
        "- Two reviews of different products or content\n"
        "- Two alerts about different services or accounts\n"
        "- Two emails that just happen to have similar subject lines\n\n"
        "IMPORTANT: Notifications are NOT the same context just because "
        "they are the same type of notification. Two email reviews, two "
        "newsletters, or two alerts are NOT the same context unless they "
        "refer to the exact same underlying thing. When in doubt, return "
        "match: false."
    ),
    "lenient": (
        "You determine whether two notifications are related enough that "
        "seeing one would help a user understand or contextualize the other.\n\n"
        "Return match: true if they share any meaningful relationship: same "
        "project area, overlapping people, related timeline, same broader "
        "initiative, or similar enough that grouping them provides value.\n\n"
        "They do NOT need to be about the exact same event — being about "
        "related topics or the same general area is sufficient.\n\n"
        "When in doubt, return match: true."
    ),
}


def _context_link_system_prompt(strictness: str) -> str:
    """Return the LLM system prompt for context link confirmation."""
    return _CONTEXT_SYSTEM_PROMPTS.get(strictness, _CONTEXT_SYSTEM_PROMPTS["balanced"])


async def confirm_context_link(
    card_a_header: str,
    card_a_summary: str,
    card_b_header: str,
    card_b_summary: str,
    space_id: str | None = None,
    strictness: str = "balanced",
) -> tuple[bool, str]:
    """LLM confirmation that two cards are about the same real-world context.

    Unlike entity-identifier comparison, this compares full card content to
    decide whether two notifications relate
    to the same underlying topic — e.g. a bill notification and a payment
    receipt for that bill.

    Injects learned context rules and recent user corrections when available
    to improve accuracy over time.

    Args:
        card_a_header: Header of the first card.
        card_a_summary: Summary of the first card.
        card_b_header: Header of the second card.
        card_b_summary: Summary of the second card.
        space_id: Optional space for rule scoping.
        strictness: Preset name controlling how strict the matching is.

    Returns:
        Tuple of (match: bool, label: str). Label is a short description
        of the shared context when match is True, empty string otherwise.
    """
    # Fetch learned rules and recent corrections for prompt injection
    feedback_section = ""
    try:
        from laya.pipeline.context_learn import (
            format_context_feedback_section,
            query_context_rules,
            query_recent_context_corrections,
        )
        rules = await query_context_rules(space_id)
        corrections = await query_recent_context_corrections(space_id, limit=10)
        feedback_section = format_context_feedback_section(rules, corrections) or ""
    except Exception as e:
        log.debug("context_feedback_injection_failed", error=str(e))

    feedback_prompt = f"\n\n{feedback_section}" if feedback_section else ""

    system_prompt = _context_link_system_prompt(strictness)

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": (
                f"Are these two notifications about the same real-world context?\n\n"
                f"Notification A:\n"
                f"  Title: {card_a_header}\n"
                f"  Summary: {card_a_summary[:300]}\n\n"
                f"Notification B:\n"
                f"  Title: {card_b_header}\n"
                f"  Summary: {card_b_summary[:300]}\n\n"
                f"{feedback_prompt}"
                f"Respond with JSON matching the schema."
            ),
        },
    ]

    schema = {
        "name": "context_match",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "match": {"type": "boolean"},
                "reasoning": {"type": "string"},
                "label": {
                    "type": "string",
                    "description": "Short label (under 50 chars) describing the shared context, e.g. 'April utility bill'. Empty if no match.",
                },
            },
            "required": ["match", "reasoning", "label"],
            "additionalProperties": False,
        },
    }

    try:
        response = await llm_call(
            role="router",  # cheap model
            messages=messages,
            response_schema=schema,
            step="context_confirm",
            temperature=0.0,
            max_tokens=DEFAULT_MAX_TOKENS,
        )

        if response.parsed and response.parsed.get("match"):
            label = response.parsed.get("label", "")
            log.info(
                "context_link_confirmed",
                card_a=card_a_header,
                card_b=card_b_header,
                label=label,
            )
            return True, label

        log.debug(
            "context_link_rejected",
            card_a=card_a_header,
            card_b=card_b_header,
            reasoning=response.parsed.get("reasoning", "") if response.parsed else "",
        )
        return False, ""

    except Exception as e:
        log.warning("context_confirm_failed", error=str(e))
        return False, ""
