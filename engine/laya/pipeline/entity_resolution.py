"""Entity Resolution Layers 2 & 3 — semantic matching + LLM confirmation."""

import json
import uuid

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call

log = structlog.get_logger()

# Cosine distance threshold for semantic matching (lower = more similar)
SEMANTIC_THRESHOLD = 0.35


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
                if distance > SEMANTIC_THRESHOLD:
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


async def confirm_entity_link(
    entity_a: str,
    entity_b: str,
    context: str = "",
) -> bool:
    """Layer 3: LLM confirmation of entity link.

    Asks a cheap model whether two entity references refer to the same thing.
    If confirmed, updates the link_method to 'llm_confirmed' with confidence 1.0.

    Args:
        entity_a: First entity reference.
        entity_b: Second entity reference.
        context: Optional context about where these entities appear.

    Returns:
        True if the LLM confirms the entities match.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You determine whether two entity references refer to the same thing. "
                "Consider aliases, abbreviations, and platform-specific identifiers."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Do these two references refer to the same entity?\n\n"
                f"Entity A: {entity_a}\n"
                f"Entity B: {entity_b}\n"
                f"{'Context: ' + context if context else ''}\n\n"
                f"Respond with JSON matching the schema."
            ),
        },
    ]

    schema = {
        "name": "entity_match",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "match": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
            "required": ["match", "reasoning"],
            "additionalProperties": False,
        },
    }

    try:
        response = await llm_call(
            role="router",  # cheap model
            messages=messages,
            response_schema=schema,
            step="entity_confirm",
            temperature=0.0,
            max_tokens=200,
        )

        if response.parsed and response.parsed.get("match"):
            # Update existing semantic links to llm_confirmed
            db = await get_db()
            await db.execute(
                """UPDATE entities
                   SET link_method = 'llm_confirmed', confidence = 1.0
                   WHERE canonical_name = ?""",
                (f"{entity_a} <-> {entity_b}",),
            )
            await db.commit()

            log.info("entity_link_confirmed", entity_a=entity_a, entity_b=entity_b)
            return True

        log.debug(
            "entity_link_rejected",
            entity_a=entity_a,
            entity_b=entity_b,
            reasoning=response.parsed.get("reasoning", "") if response.parsed else "",
        )
        return False

    except Exception as e:
        log.warning("entity_confirm_failed", error=str(e))
        return False
