# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Context-group management — merge/unlink/related + rolling group summaries (split from cards_api — P7-6)."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.api.websocket import manager
from laya.db.sqlite import get_db, transaction
from laya.models.card import (
    GroupSummaryResponse,
)

log = structlog.get_logger()
router = APIRouter()


class MergeCardsRequest(BaseModel):
    card_ids: list[str]


@router.get("/cards/groups/{context_id}")
async def get_context_group(context_id: str):
    """Get context group metadata and member entity_ids."""
    db = await get_db()
    group_row = await db.execute_fetchall(
        "SELECT * FROM context_groups WHERE context_id = ?", (context_id,)
    )
    if not group_row:
        raise HTTPException(status_code=404, detail="Context group not found")

    members = await db.execute_fetchall(
        "SELECT card_id, confidence, link_method, added_at FROM context_group_members WHERE context_id = ?",
        (context_id,),
    )
    cards = await db.execute_fetchall(
        "SELECT card_id, header, entity_id, status FROM action_cards WHERE context_id = ?",
        (context_id,),
    )

    return {
        "context_id": context_id,
        "label": group_row[0]["label"],
        "user_confirmed": bool(group_row[0]["user_confirmed"]),
        "user_split": bool(group_row[0]["user_split"]),
        "created_at": group_row[0]["created_at"],
        "members": [dict(m) for m in members],
        "cards": [{"card_id": c["card_id"], "header": c["header"], "entity_id": c["entity_id"], "status": c["status"]} for c in cards],
    }


@router.get("/cards/{card_id}/related")
async def get_related_cards(card_id: str):
    """Return individual cards related to this card via context association."""
    db = await get_db()

    card_row = await db.execute_fetchall(
        "SELECT entity_id, context_id FROM action_cards WHERE card_id = ?", (card_id,),
    )
    if not card_row:
        raise HTTPException(status_code=404, detail="Card not found")

    # Find all non-split context groups containing this card
    ctx_rows = await db.execute_fetchall(
        """SELECT m.context_id, g.label
           FROM context_group_members m
           JOIN context_groups g ON g.context_id = m.context_id
           WHERE m.card_id = ? AND g.user_split = FALSE""",
        (card_id,),
    )
    if not ctx_rows:
        return {"card_id": card_id, "related_cards": [], "total_related_cards": 0}

    ctx_ids = list({r["context_id"] for r in ctx_rows})
    ctx_labels = {r["context_id"]: r["label"] for r in ctx_rows}

    # Get all OTHER card_ids in those context groups
    placeholders = ",".join("?" * len(ctx_ids))
    member_rows = await db.execute_fetchall(
        f"""SELECT m.card_id, m.context_id, m.confidence, m.link_method
            FROM context_group_members m
            WHERE m.context_id IN ({placeholders}) AND m.card_id != ?""",
        ctx_ids + [card_id],
    )
    if not member_rows:
        return {"card_id": card_id, "related_cards": [], "total_related_cards": 0}

    # Fetch card details
    related_card_ids = list({r["card_id"] for r in member_rows})
    cp = ",".join("?" * len(related_card_ids))
    card_rows = await db.execute_fetchall(
        f"SELECT card_id, header, entity_id, status FROM action_cards WHERE card_id IN ({cp})",
        related_card_ids,
    )

    member_info: dict[str, dict] = {}
    for r in member_rows:
        cid = r["card_id"]
        if cid not in member_info or r["confidence"] > member_info[cid].get("confidence", 0):
            member_info[cid] = {"context_id": r["context_id"], "confidence": r["confidence"], "link_method": r["link_method"]}

    related = []
    for c in card_rows:
        info = member_info.get(c["card_id"], {})
        related.append({
            "card_id": c["card_id"],
            "header": c["header"],
            "entity_id": c["entity_id"],
            "status": c["status"],
            "context_id": info.get("context_id", ""),
            "context_label": ctx_labels.get(info.get("context_id", ""), ""),
            "confidence": info.get("confidence", 0),
            "link_method": info.get("link_method", ""),
        })

    related.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "card_id": card_id,
        "related_cards": related,
        "total_related_cards": len(related),
    }


@router.post("/cards/{card_id}/unlink-related")
async def unlink_related_card(card_id: str):
    """Remove a single card from all its context groups."""
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT entity_id FROM action_cards WHERE card_id = ?", (card_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")

    # Find all context groups this card belongs to
    memberships = await db.execute_fetchall(
        "SELECT context_id FROM context_group_members WHERE card_id = ?", (card_id,),
    )
    affected_ctx_ids = [r["context_id"] for r in memberships]

    # Remove the card from its groups and dissolve any left with <=1 member as one
    # invariant — otherwise the card could be detached but its now-singleton group
    # never marked split (review §2 API — P4-12).
    async with transaction():
        await db.execute("DELETE FROM context_group_members WHERE card_id = ?", (card_id,))
        await db.execute("UPDATE action_cards SET context_id = NULL WHERE card_id = ?", (card_id,))

        # Dissolve context groups that have <=1 member remaining
        for ctx_id in affected_ctx_ids:
            remaining = await db.execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM context_group_members WHERE context_id = ?", (ctx_id,),
            )
            if remaining[0]["cnt"] <= 1:
                await db.execute(
                    "UPDATE context_groups SET user_split = TRUE WHERE context_id = ?", (ctx_id,),
                )

    log.info("card_unlinked_related", card_id=card_id, groups=affected_ctx_ids)
    await manager.broadcast({"type": "context_group_unlinked", "payload": {"card_id": card_id}})
    return {"status": "unlinked", "card_id": card_id}


@router.post("/cards/groups/{context_id}/unlink")
async def unlink_context_group(context_id: str):
    """Split a context group — cards revert to entity_id grouping.

    Sets user_split=TRUE so the system won't re-merge these cards.
    """
    db = await get_db()
    group_row = await db.execute_fetchall(
        "SELECT context_id FROM context_groups WHERE context_id = ?", (context_id,)
    )
    if not group_row:
        raise HTTPException(status_code=404, detail="Context group not found")

    # Fetch cards in this group before unlinking (for correction recording)
    group_cards = await db.execute_fetchall(
        """SELECT c.card_id, c.header, c.summary, c.space_id, e.source_platform
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           WHERE c.context_id = ?""",
        (context_id,),
    )

    # Split the group as one invariant: mark user_split so resolve_context_group
    # won't re-merge, and detach every member card. A half-applied split (flag set
    # but cards still linked, or vice-versa) would let the group silently re-form
    # (review §2 API — P4-12).
    async with transaction():
        await db.execute(
            "UPDATE context_groups SET user_split = TRUE WHERE context_id = ?",
            (context_id,),
        )
        await db.execute(
            "UPDATE action_cards SET context_id = NULL WHERE context_id = ?",
            (context_id,),
        )

    # Record unlink corrections for learning (best-effort, outside the invariant —
    # anchor-based: first card paired with each other).
    if len(group_cards) >= 2:
        space_id = group_cards[0]["space_id"]
        anchor = group_cards[0]
        for other in group_cards[1:]:
            try:
                await db.execute(
                    """INSERT INTO context_corrections
                       (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                        platform_a, platform_b, action, space_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unlink', ?)""",
                    (anchor["card_id"], other["card_id"], anchor["header"], other["header"],
                     anchor["summary"], other["summary"],
                     anchor["source_platform"], other["source_platform"], space_id),
                )
            except Exception as e:
                log.debug("context_correction_insert_failed", error=str(e))
        await db.commit()

    log.info("context_group_unlinked", context_id=context_id)
    await manager.broadcast({"type": "context_group_unlinked", "payload": {"context_id": context_id}})
    return {"status": "unlinked", "context_id": context_id}


@router.post("/cards/groups/merge")
async def merge_cards(body: MergeCardsRequest):
    """Manually merge cards into a context group.

    Creates a user-confirmed context group for the specified cards.
    """
    import uuid

    if len(body.card_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 card_ids required")

    db = await get_db()

    # Fetch all specified cards (include summary + platform for correction recording)
    placeholders = ",".join("?" * len(body.card_ids))
    cards = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id, c.context_id, c.header, c.summary, c.space_id,
                   e.source_platform
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            WHERE c.card_id IN ({placeholders})""",
        body.card_ids,
    )
    if len(cards) < 2:
        raise HTTPException(status_code=404, detail="Not enough valid cards found")

    # Check if any card already belongs to a context group — extend it
    existing_context_id = None
    for c in cards:
        if c["context_id"]:
            existing_context_id = c["context_id"]
            break

    if existing_context_id:
        context_id = existing_context_id
    else:
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"

    # Create/confirm the group and assign every card as one invariant — a
    # half-applied merge would leave cards pointing at a context_groups row that
    # was never created, or a group with only some of its members (review §2 API
    # — P4-12).
    async with transaction():
        if existing_context_id:
            # Update the group to user-confirmed and clear any user_split
            await db.execute(
                "UPDATE context_groups SET user_confirmed = TRUE, user_split = FALSE WHERE context_id = ?",
                (context_id,),
            )
        else:
            # Use the first card's header as the label
            label = cards[0]["header"]
            if len(label) > 60:
                label = label[:57] + "..."
            await db.execute(
                "INSERT INTO context_groups (context_id, label, user_confirmed) VALUES (?, ?, TRUE)",
                (context_id, label),
            )

        # Assign context_id to all cards and register card-level memberships
        for c in cards:
            await db.execute(
                "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
                (context_id, c["card_id"]),
            )
            await db.execute(
                "INSERT OR IGNORE INTO context_group_members (context_id, card_id, confidence, link_method) VALUES (?, ?, 1.0, 'user')",
                (context_id, c["card_id"]),
            )

    # Record link corrections for learning (anchor-based: first card paired with each other)
    space_id = cards[0]["space_id"] if cards else None
    anchor = cards[0]
    for other in cards[1:]:
        # Only record pairs from different entity groups (same-entity links are redundant)
        if anchor["entity_id"] and other["entity_id"] and anchor["entity_id"] == other["entity_id"]:
            continue
        try:
            await db.execute(
                """INSERT INTO context_corrections
                   (card_id_a, card_id_b, header_a, header_b, summary_a, summary_b,
                    platform_a, platform_b, action, space_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'link', ?)""",
                (anchor["card_id"], other["card_id"], anchor["header"], other["header"],
                 anchor["summary"], other["summary"],
                 anchor["source_platform"], other["source_platform"], space_id),
            )
        except Exception as e:
            log.debug("context_correction_insert_failed", error=str(e))
    await db.commit()

    log.info("context_group_merged", context_id=context_id, card_count=len(cards))
    await manager.broadcast({"type": "context_group_merged", "payload": {"context_id": context_id}})
    return {"status": "merged", "context_id": context_id, "card_count": len(cards)}


# ---------------------------------------------------------------------------
# Group summary endpoints
# ---------------------------------------------------------------------------


@router.get("/cards/groups/{entity_id:path}/summary")
async def get_group_summary(entity_id: str):
    """Return the rolling summary for an entity group."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM group_summaries WHERE entity_id = ?",
        (entity_id,),
    )
    row = rows[0] if rows else None
    if not row:
        raise HTTPException(status_code=404, detail="No summary for this entity group")
    return GroupSummaryResponse(
        entity_id=row["entity_id"],
        headline=row["headline"],
        summary=row["summary"],
        key_events=json.loads(row["key_events"] or "null"),
        current_status=row["current_status"],
        pending_actions=json.loads(row["pending_actions"] or "null"),
        card_count=row["card_count"],
        card_ids=json.loads(row["card_ids"] or "[]"),
        updated_at=row["updated_at"],
    )


@router.post("/cards/groups/{entity_id:path}/summary/regenerate")
async def regenerate_summary(entity_id: str):
    """Force full regeneration of a group summary from all cards."""
    from laya.pipeline.group_summary import regenerate_group_summary

    result = await regenerate_group_summary(entity_id)
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Could not generate summary — entity group may have fewer than 2 cards or LLM call failed",
        )
    return result
