"""Cards REST API — list, detail, approve, dismiss action cards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.models.card import CardResponse, CardsListResponse, StagedOutput, SuggestedAction

log = structlog.get_logger()
router = APIRouter()


def _row_to_card(row) -> CardResponse:
    """Convert a SQLite Row to a CardResponse, deserializing JSON columns."""
    intelligence = None
    if row["intelligence"]:
        try:
            intelligence = json.loads(row["intelligence"])
        except json.JSONDecodeError:
            intelligence = None

    staged_output = None
    if row["staged_output"]:
        try:
            staged_output = StagedOutput(**json.loads(row["staged_output"]))
        except (json.JSONDecodeError, Exception):
            staged_output = None

    suggested_actions = None
    if row["suggested_actions"]:
        try:
            raw_actions = json.loads(row["suggested_actions"])
            suggested_actions = [SuggestedAction(**a) for a in raw_actions]
        except (json.JSONDecodeError, Exception):
            suggested_actions = None

    return CardResponse(
        card_id=row["card_id"],
        event_id=row["event_id"],
        created_at=row["created_at"],
        priority=row["priority"],
        persona=row["persona"],
        category=row["category"],
        header=row["header"],
        summary=row["summary"],
        intelligence=intelligence,
        staged_output=staged_output,
        suggested_actions=suggested_actions,
        status=row["status"],
        privacy_tier=row["privacy_tier"] or 2,
        has_workspace=bool(row["has_workspace"]),
        resolved_at=row["resolved_at"],
        user_feedback=row["user_feedback"],
        feedback_type=row["feedback_type"],
        confidence=row["confidence"],
        router_model=row["router_model"],
        stager_model=row["stager_model"],
        updated_at=row["updated_at"],
    )


@router.get("/cards")
async def list_cards(
    status: str | None = None,
    priority: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "created_at_desc",
) -> CardsListResponse:
    """List action cards with optional filters and sorting."""
    db = await get_db()

    # Build WHERE clause
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if priority:
        conditions.append("priority = ?")
        params.append(priority)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Sort
    sort_map = {
        "created_at_desc": "created_at DESC",
        "created_at_asc": "created_at ASC",
        "priority_desc": """CASE priority
            WHEN 'CRITICAL' THEN 0
            WHEN 'HIGH' THEN 1
            WHEN 'MEDIUM' THEN 2
            WHEN 'LOW' THEN 3
            END ASC""",
    }
    order_by = sort_map.get(sort, "created_at DESC")

    # Count total
    count_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) FROM action_cards {where_clause}", params
    )
    total = count_rows[0][0]

    # Fetch page
    rows = await db.execute_fetchall(
        f"""SELECT card_id, event_id, created_at, priority, persona, category,
                   header, summary, intelligence, staged_output, suggested_actions,
                   status, privacy_tier, has_workspace, resolved_at, user_feedback,
                   feedback_type, confidence, router_model, stager_model, updated_at
            FROM action_cards
            {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )

    cards = [_row_to_card(r) for r in rows]

    return CardsListResponse(cards=cards, total=total, limit=limit, offset=offset)


@router.get("/cards/{card_id}")
async def get_card(card_id: str) -> CardResponse:
    """Get full action card detail."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT card_id, event_id, created_at, priority, persona, category,
                  header, summary, intelligence, staged_output, suggested_actions,
                  status, privacy_tier, has_workspace, resolved_at, user_feedback,
                  feedback_type, confidence, router_model, stager_model, updated_at
           FROM action_cards
           WHERE card_id = ?""",
        (card_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    return _row_to_card(rows[0])


class ApproveRequest(BaseModel):
    modifications: dict[str, Any] | None = None


@router.post("/cards/{card_id}/approve")
async def approve_card(card_id: str, body: ApproveRequest | None = None) -> dict:
    """Approve an action card."""
    db = await get_db()

    # Check current status
    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    approvable = {"pending", "agent_running", "awaiting_input", "staged"}
    if rows[0]["status"] not in approvable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' cannot be approved"
        )

    now = datetime.now(timezone.utc).isoformat()
    modifications_json = None
    if body and body.modifications:
        modifications_json = json.dumps(body.modifications)

    await db.execute(
        """UPDATE action_cards
           SET status = 'approved', resolved_at = ?, user_feedback = ?,
               updated_at = ?
           WHERE card_id = ?""",
        (now, modifications_json, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "approved"}}
    )

    log.info("card_approved", card_id=card_id)
    return {"status": "approved", "card_id": card_id}


class DismissRequest(BaseModel):
    reason: str | None = None
    feedback_type: str | None = None


@router.post("/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str, body: DismissRequest | None = None) -> dict:
    """Dismiss an action card with optional feedback."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    terminal = {"completed", "failed", "dismissed"}
    if rows[0]["status"] in terminal:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' is terminal"
        )

    now = datetime.now(timezone.utc).isoformat()
    reason = body.reason if body else None
    feedback_type = body.feedback_type if body else None

    await db.execute(
        """UPDATE action_cards
           SET status = 'dismissed', resolved_at = ?, user_feedback = ?,
               feedback_type = ?, updated_at = ?
           WHERE card_id = ?""",
        (now, reason, feedback_type, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "dismissed"}}
    )

    log.info("card_dismissed", card_id=card_id, feedback_type=feedback_type)
    return {"status": "dismissed", "card_id": card_id}
