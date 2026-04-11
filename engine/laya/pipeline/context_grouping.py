"""Semantic context grouping — group cards about the same real-world context.

Cards with different entity_ids (e.g. two Gmail threads) may still be about
the same underlying topic (e.g. a utility bill).  This module detects such
relationships via ChromaDB similarity search and optional LLM confirmation,
then assigns a shared context_id so the feed can group them together.
"""

import uuid

import structlog

from laya.config import load_settings
from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db

log = structlog.get_logger()


async def resolve_context_group(
    card_id: str,
    entity_id: str,
    embed_text: str,
    space_id: str | None,
    platform: str,
) -> str | None:
    """Find or create a context group for a newly emitted card.

    Searches ChromaDB for semantically similar cards with *different* entity_ids.
    If a strong match is found, assigns both cards to the same context_id.

    Non-critical — failures are logged but never block emit.

    Args:
        card_id: The card being emitted.
        entity_id: The card's structural entity_id.
        embed_text: The card's embedding text (already indexed in ChromaDB).
        space_id: Space boundary — only group within same space.
        platform: Source platform of the card.

    Returns:
        context_id if a group was found/created, None otherwise.
    """
    settings = load_settings()
    sg_config = settings.get("smart_grouping", {})
    if not sg_config.get("context_association", True):
        return None

    confidence_threshold = sg_config.get("confidence_threshold", 0.30)
    auto_confirm_threshold = sg_config.get("auto_confirm_threshold", 0.20)

    # Search ChromaDB for semantically similar cards
    where_filter: dict | None = None
    if space_id:
        where_filter = {"space_id": space_id}

    try:
        results = await memory_search(
            embed_text,
            n_results=5,
            where=where_filter,
            max_distance=confidence_threshold,
        )
    except Exception as e:
        log.warning("context_group_search_failed", card_id=card_id, error=str(e))
        return None

    if not results:
        return None

    db = await get_db()

    # Filter: exclude self and cards with the same entity_id
    candidates = []
    for result in results:
        metadata = result.get("metadata", {})
        result_card_id = metadata.get("card_id", "")
        if result_card_id == card_id:
            continue

        # Look up the candidate's entity_id and context_id
        row = await db.execute_fetchall(
            "SELECT entity_id, context_id, header, summary FROM action_cards WHERE card_id = ?",
            (result_card_id,),
        )
        if not row:
            continue

        candidate_entity_id = row[0]["entity_id"]
        if candidate_entity_id == entity_id:
            # Already in the same entity group — skip
            continue

        candidates.append({
            "card_id": result_card_id,
            "entity_id": candidate_entity_id,
            "context_id": row[0]["context_id"],
            "header": row[0]["header"],
            "summary": row[0]["summary"],
            "distance": result.get("distance", 1.0),
        })

    if not candidates:
        return None

    # Sort by distance (most similar first)
    candidates.sort(key=lambda c: c["distance"])
    best = candidates[0]

    # Check if this candidate's context group was user-split
    if best["context_id"]:
        split_row = await db.execute_fetchall(
            "SELECT user_split FROM context_groups WHERE context_id = ?",
            (best["context_id"],),
        )
        if split_row and split_row[0]["user_split"]:
            log.debug(
                "context_group_user_split",
                card_id=card_id,
                context_id=best["context_id"],
            )
            return None

    # Confidence tiers
    if best["distance"] <= auto_confirm_threshold:
        # High confidence — assign directly
        log.info(
            "context_group_auto",
            card_id=card_id,
            matched_card=best["card_id"],
            distance=best["distance"],
        )
    elif best["distance"] <= confidence_threshold:
        # Medium confidence — LLM confirmation needed
        # Get current card's header/summary for the LLM
        current_row = await db.execute_fetchall(
            "SELECT header, summary FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        if not current_row:
            return None

        from laya.pipeline.entity_resolution import confirm_context_link

        confirmed, label = await confirm_context_link(
            card_a_header=current_row[0]["header"],
            card_a_summary=current_row[0]["summary"],
            card_b_header=best["header"],
            card_b_summary=best["summary"],
            space_id=space_id,
        )
        if not confirmed:
            log.debug(
                "context_group_rejected_by_llm",
                card_id=card_id,
                matched_card=best["card_id"],
                distance=best["distance"],
            )
            return None

        log.info(
            "context_group_llm_confirmed",
            card_id=card_id,
            matched_card=best["card_id"],
            distance=best["distance"],
        )
    else:
        return None

    # Assign context_id
    if best["context_id"]:
        # Join existing context group
        context_id = best["context_id"]
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, entity_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, entity_id, 1.0 - best["distance"], "semantic"),
        )
        await db.commit()
    else:
        # Create new context group for both cards
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"

        # Generate a label from the two card headers
        group_label = await _generate_context_label(
            best["header"], best.get("summary", ""),
            card_id, db,
        )

        await db.execute(
            "INSERT INTO context_groups (context_id, label) VALUES (?, ?)",
            (context_id, group_label),
        )
        # Add both entity_ids as members
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, entity_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, best["entity_id"], 1.0 - best["distance"], "semantic"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, entity_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, entity_id, 1.0 - best["distance"], "semantic"),
        )
        # Assign context_id to the matched card too
        await db.execute(
            "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
            (context_id, best["card_id"]),
        )
        await db.commit()

    return context_id


async def _generate_context_label(
    matched_header: str,
    matched_summary: str,
    current_card_id: str,
    db,
) -> str:
    """Generate a short label for a context group.

    Uses a simple heuristic: takes the matched card's header and truncates it.
    A more sophisticated version could use LLM to synthesize a label from
    both card headers.
    """
    # Get current card header for potential LLM synthesis
    current_row = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?",
        (current_card_id,),
    )
    current_header = current_row[0]["header"] if current_row else ""

    # Simple heuristic: use the shorter header as a starting point,
    # or common words between the two
    if len(matched_header) <= len(current_header):
        label = matched_header
    else:
        label = current_header

    # Truncate to reasonable length
    if len(label) > 60:
        label = label[:57] + "..."

    return label
