# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Actions REST API — execute approved actions via n8n."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.db.sqlite import get_db
from laya.pipeline.executor import execute_action

log = structlog.get_logger()
router = APIRouter()


class ExecuteActionRequest(BaseModel):
    card_id: str
    action_id: str
    modifications: dict[str, Any] | None = None


@router.post("/actions/execute")
async def execute_action_endpoint(body: ExecuteActionRequest) -> dict:
    """Execute a suggested action from an action card.

    Looks up the suggested_action, forwards to n8n webhook,
    records result in action_log, updates card status.
    """
    try:
        result = await execute_action(
            card_id=body.card_id,
            action_id=body.action_id,
            modifications=body.modifications,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("execute_action_failed", card_id=body.card_id, error=str(e))
        raise HTTPException(status_code=500, detail="Action execution failed")


@router.post("/actions/{action_id}/retry")
async def retry_action_endpoint(action_id: str) -> dict:
    """Retry a failed action that was marked as retryable.

    Re-executes the action with the original card_id and modifications.
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT action_id, card_id, payload, modifications, retryable
           FROM action_log WHERE action_id = ?""",
        (action_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

    row = rows[0]
    if not row["retryable"]:
        raise HTTPException(status_code=400, detail="Action is not retryable")

    card_id = row["card_id"]
    modifications = json.loads(row["modifications"]) if row["modifications"] else None

    # Reset card status to allow re-execution
    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(card_id, "ready", actor="user")
    except ValueError:
        pass
    # Delete old action_log entry so action_id can be reused
    await db.execute("DELETE FROM action_log WHERE action_id = ?", (action_id,))
    await db.commit()

    try:
        result = await execute_action(
            card_id=card_id,
            action_id=action_id,
            modifications=modifications,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("retry_action_failed", action_id=action_id, error=str(e))
        raise HTTPException(status_code=500, detail="Action retry failed")
