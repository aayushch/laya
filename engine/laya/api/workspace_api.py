"""Workspace API — fetch agent session state for a card."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()


@router.get("/cards/{card_id}/workspace")
async def get_workspace(card_id: str) -> dict[str, Any]:
    """Fetch workspace state for a card.

    Returns the most recent session and its timeline events,
    plus context extracted from the router output.
    """
    db = await get_db()

    # Get the most recent session for this card
    session_row = await db.execute_fetchall(
        """SELECT session_id, card_id, agent_type, status,
                  repo_path, initial_prompt, started_at, updated_at,
                  completed_at, findings_json, error_message
           FROM workspace_sessions
           WHERE card_id = ?
           ORDER BY started_at DESC
           LIMIT 1""",
        (card_id,),
    )

    if not session_row:
        return {"card_id": card_id, "session": None, "events": [], "context": {}}

    row = session_row[0]
    session = {
        "session_id": row[0],
        "agent_type": row[2],
        "status": row[3],
        "repo_path": row[4],
        "started_at": row[6],
        "updated_at": row[7],
        "completed_at": row[8],
        "findings": json.loads(row[9]) if row[9] else None,
        "error_message": row[10],
    }

    # Get workspace events for this session
    event_rows = await db.execute_fetchall(
        """SELECT event_id, timestamp, event_type, actor, content, requires_input
           FROM workspace_events
           WHERE session_id = ?
           ORDER BY timestamp ASC""",
        (row[0],),
    )

    events = [
        {
            "event_id": e[0],
            "timestamp": e[1],
            "event_type": e[2],
            "actor": e[3],
            "content": json.loads(e[4]) if e[4] else {},
            "requires_input": bool(e[5]),
        }
        for e in event_rows
    ]

    # Build context from the router output stored on the event
    context = await _build_context(card_id)

    return {
        "card_id": card_id,
        "session": session,
        "events": events,
        "context": context,
    }


async def _build_context(card_id: str) -> dict[str, Any]:
    """Build context from the card's router output (entities, research plan)."""
    db = await get_db()

    # Get router_output from the events table via the action_cards FK
    card_rows = await db.execute_fetchall(
        """SELECT e.router_output
           FROM action_cards ac
           JOIN events e ON ac.event_id = e.event_id
           WHERE ac.card_id = ?""",
        (card_id,),
    )

    if not card_rows or not card_rows[0][0]:
        return {}

    try:
        router_output = json.loads(card_rows[0][0])
        context: dict[str, Any] = {}

        entities = router_output.get("entities", [])
        if entities:
            context["related_entities"] = entities

        research_plan = router_output.get("research_plan", [])
        if research_plan:
            context["research_plan"] = research_plan

        return context
    except (json.JSONDecodeError, AttributeError):
        return {}
