"""Actions REST API — execute approved actions via n8n."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
