"""Chat REST API — send messages and retrieve history."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, HTTPException

from laya.db.sqlite import get_db
from laya.models.chat import ChatMessage, ChatRequest, ChatResponse
from laya.pipeline.chat import process_chat_message

log = structlog.get_logger()
router = APIRouter()


@router.post("/chat")
async def send_chat_message(body: ChatRequest) -> ChatResponse:
    """Send a chat message and receive an AI response."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        response = await process_chat_message(
            body.message.strip(),
            space_id=body.space_id,
        )
        return response
    except Exception as e:
        log.error("chat_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Chat processing failed")


@router.get("/chat/history")
async def get_chat_history(limit: int = 50, before: str | None = None) -> list[ChatMessage]:
    """Get chat message history, newest first."""
    db = await get_db()

    if before:
        rows = await db.execute_fetchall(
            """SELECT message_id, timestamp, role, content,
                      referenced_cards, referenced_events
               FROM chat_messages
               WHERE timestamp < ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (before, limit),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT message_id, timestamp, role, content,
                      referenced_cards, referenced_events
               FROM chat_messages
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        )

    messages = []
    for row in rows:
        ref_cards = json.loads(row["referenced_cards"]) if row["referenced_cards"] else []
        ref_events = json.loads(row["referenced_events"]) if row["referenced_events"] else []
        messages.append(
            ChatMessage(
                message_id=row["message_id"],
                timestamp=row["timestamp"],
                role=row["role"],
                content=row["content"],
                referenced_cards=ref_cards,
                referenced_events=ref_events,
            )
        )

    return messages
