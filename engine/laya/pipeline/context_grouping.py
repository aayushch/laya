"""Semantic context grouping — group cards about the same real-world context.

Cards with different entity_ids (e.g. two Gmail threads) may still be about
the same underlying topic (e.g. a utility bill).  This module detects such
relationships via ChromaDB similarity search and optional LLM confirmation,
then assigns a shared context_id so the feed can group them together.

Strictness presets control how aggressively cards are linked:
- strict: same issue across different platforms (requires shared identifiers)
- balanced: same broader context/topic
- lenient: related notifications that provide mutual context
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

import structlog

from laya.config import load_settings
from laya.db.chromadb_store import get_collection, memory_search
from laya.db.sqlite import get_db
from laya.pipeline.context_presets import (
    _entity_refs_overlap,
    get_strictness,
    resolve_context_config,
)

log = structlog.get_logger()

_SOFT_BOOST_DISCOUNT = 0.05


async def resolve_context_group(
    card_id: str,
    entity_id: str,
    embed_text: str,
    space_id: str | None,
    platform: str,
    entity_refs: str = "",
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
        entity_refs: Comma-separated entity references for overlap checking.

    Returns:
        context_id if a group was found/created, None otherwise.
    """
    settings = load_settings()
    sg_config = settings.get("smart_grouping", {})
    if not sg_config.get("context_association", True):
        return None

    ctx_config = resolve_context_config(sg_config)
    strictness = get_strictness(sg_config)
    confidence_threshold = ctx_config["confidence_threshold"]
    auto_confirm_threshold = ctx_config["auto_confirm_threshold"]
    cross_platform_required = ctx_config["cross_platform_required"]
    entity_ref_overlap_mode = ctx_config["entity_ref_overlap_mode"]
    always_llm = ctx_config["always_llm"]

    # Legacy cross_platform_grouping toggle (restricts to same platform)
    cross_platform = sg_config.get("cross_platform_grouping", True)

    # Time window: only consider cards from the last N days
    tuning = settings.get("tuning", {})
    time_window_days = tuning.get("context_association_time_window_days", 7)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=time_window_days)).timestamp()

    # Search ChromaDB for semantically similar cards
    # Filter by space, time window, and platform constraints
    and_conditions: list[dict] = [{"timestamp": {"$gte": cutoff}}]
    if space_id:
        and_conditions.append({"space_id": space_id})
    if not cross_platform:
        and_conditions.append({"source_platform": platform})
    elif cross_platform_required:
        and_conditions.append({"source_platform": {"$ne": platform}})

    where_filter: dict | None
    if len(and_conditions) == 1:
        where_filter = and_conditions[0]
    else:
        where_filter = {"$and": and_conditions}

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

        # Cross-platform enforcement: double-check after retrieval
        result_platform = metadata.get("source_platform", "")
        if cross_platform_required and result_platform == platform:
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
            continue

        candidate_refs = metadata.get("entity_refs", "")
        distance = result.get("distance", 1.0)

        # Entity ref overlap check
        has_ref_overlap = _entity_refs_overlap(entity_refs, candidate_refs)
        if entity_ref_overlap_mode == "hard_gate" and not has_ref_overlap:
            log.debug(
                "context_group_no_ref_overlap",
                card_id=card_id,
                candidate=result_card_id,
                distance=distance,
            )
            continue
        elif entity_ref_overlap_mode == "soft_boost" and has_ref_overlap:
            distance = max(0.0, distance - _SOFT_BOOST_DISCOUNT)

        candidates.append({
            "card_id": result_card_id,
            "entity_id": candidate_entity_id,
            "context_id": row[0]["context_id"],
            "header": row[0]["header"],
            "summary": row[0]["summary"],
            "distance": distance,
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
    if not always_llm and auto_confirm_threshold and best["distance"] <= auto_confirm_threshold:
        log.info(
            "context_group_auto",
            card_id=card_id,
            matched_card=best["card_id"],
            distance=best["distance"],
            strictness=strictness,
        )
    elif best["distance"] <= confidence_threshold:
        # LLM confirmation needed
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
            strictness=strictness,
        )
        if not confirmed:
            log.debug(
                "context_group_rejected_by_llm",
                card_id=card_id,
                matched_card=best["card_id"],
                distance=best["distance"],
                strictness=strictness,
            )
            return None

        log.info(
            "context_group_llm_confirmed",
            card_id=card_id,
            matched_card=best["card_id"],
            distance=best["distance"],
            strictness=strictness,
        )
    else:
        return None

    # Assign context_id
    if best["context_id"]:
        # Join existing context group — validate against group centroid first
        context_id = best["context_id"]
        centroid_thresh = ctx_config["centroid_threshold"]
        if not await _passes_centroid_check(context_id, card_id, centroid_thresh):
            log.info("context_group_centroid_rejected", card_id=card_id, context_id=context_id)
            return None
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, card_id, 1.0 - best["distance"], "semantic"),
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
        # Add both cards as members
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, best["card_id"], 1.0 - best["distance"], "semantic"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (context_id, card_id, 1.0 - best["distance"], "semantic"),
        )
        # Assign context_id to the matched card too
        await db.execute(
            "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
            (context_id, best["card_id"]),
        )
        await db.commit()

    return context_id


async def _passes_centroid_check(
    context_id: str,
    new_card_id: str,
    centroid_threshold: float,
) -> bool:
    """Validate a new card against the centroid of an existing context group.

    Prevents semantic chaining — each new card must be close to the group's
    center of mass, not just one outlier member.  Pure embedding math, no LLM.

    Returns True if the card passes (or on any error — fail-open).
    """
    try:
        import numpy as np
    except ImportError:
        return True

    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT card_id FROM context_group_members WHERE context_id = ?",
            (context_id,),
        )
        member_ids = [r["card_id"] for r in rows]
        if len(member_ids) < 3:
            return True

        sample = random.sample(member_ids, min(10, len(member_ids)))
        collection = get_collection()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(collection.get, ids=sample + [new_card_id], include=["embeddings"]),
        )

        embeddings = result.get("embeddings")
        ids = result.get("ids", [])
        if not embeddings or new_card_id not in ids:
            return True

        new_idx = ids.index(new_card_id)
        new_emb = np.array(embeddings[new_idx], dtype=np.float32)
        member_embs = np.array(
            [embeddings[i] for i in range(len(ids)) if i != new_idx],
            dtype=np.float32,
        )
        if member_embs.shape[0] == 0:
            return True

        centroid = member_embs.mean(axis=0)
        dot = np.dot(new_emb, centroid)
        norms = np.linalg.norm(new_emb) * np.linalg.norm(centroid)
        if norms == 0:
            return True
        distance = 1.0 - float(dot / norms)

        if distance > centroid_threshold:
            log.info(
                "centroid_check_failed",
                new_card_id=new_card_id,
                context_id=context_id,
                distance=round(distance, 4),
                threshold=centroid_threshold,
                group_size=len(member_ids),
            )
            return False
        return True
    except Exception as e:
        log.warning("centroid_check_error", context_id=context_id, error=str(e))
        return True


async def assign_or_join_context_group(
    card_id: str,
    matched_card_id: str,
    label: str,
    space_id: str | None,
    entity_refs: str = "",
    matched_entity_refs: str = "",
) -> str | None:
    """Assign a card to a context group with the matched card.

    If the matched card already belongs to a context group, the new card joins it.
    If not, a new context group is created for both cards.

    Respects user_split — if the user has manually split a group, we don't rejoin.
    In strict mode, validates entity_ref overlap before accepting stager matches.

    Returns:
        context_id if assigned, None if rejected (e.g. user_split).
    """
    settings = load_settings()
    sg_config = settings.get("smart_grouping", {})
    ctx_config = resolve_context_config(sg_config)

    # In strict/hard_gate mode, validate entity ref overlap for stager matches
    if ctx_config["entity_ref_overlap_mode"] == "hard_gate":
        if not _entity_refs_overlap(entity_refs, matched_entity_refs):
            log.debug(
                "context_group_stager_no_ref_overlap",
                card_id=card_id,
                matched_card_id=matched_card_id,
            )
            return None
    db = await get_db()

    # Look up the matched card's current context state
    match_rows = await db.execute_fetchall(
        "SELECT context_id, header, summary FROM action_cards WHERE card_id = ?",
        (matched_card_id,),
    )
    if not match_rows:
        return None

    matched_context_id = match_rows[0]["context_id"]
    matched_header = match_rows[0]["header"]

    # Check user_split on existing group
    if matched_context_id:
        split_rows = await db.execute_fetchall(
            "SELECT user_split FROM context_groups WHERE context_id = ?",
            (matched_context_id,),
        )
        if split_rows and split_rows[0]["user_split"]:
            log.debug("context_group_user_split_stager", card_id=card_id)
            return None

        # Join existing group — validate against group centroid first
        centroid_threshold = ctx_config["centroid_threshold"]
        if not await _passes_centroid_check(matched_context_id, card_id, centroid_threshold):
            log.info("context_group_centroid_rejected_stager", card_id=card_id, context_id=matched_context_id)
            return None
        await db.execute(
            "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
            (matched_context_id, card_id, 1.0, "stager"),
        )
        await db.commit()
        return matched_context_id

    # No existing group — create a new one
    context_id = f"ctx_{uuid.uuid4().hex[:12]}"
    group_label = label if label else _truncate_label(matched_header)

    await db.execute(
        "INSERT INTO context_groups (context_id, label) VALUES (?, ?)",
        (context_id, group_label),
    )
    await db.execute(
        "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
        (context_id, matched_card_id, 1.0, "stager"),
    )
    await db.execute(
        "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, ?, ?)",
        (context_id, card_id, 1.0, "stager"),
    )
    # Assign context_id to the matched card too
    await db.execute(
        "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
        (context_id, matched_card_id),
    )
    await db.commit()
    return context_id


def _truncate_label(header: str) -> str:
    """Truncate a header to use as a context group label."""
    if len(header) > 60:
        return header[:57] + "..."
    return header


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

    return _truncate_label(label)
