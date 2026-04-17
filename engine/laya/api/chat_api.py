"""Chat REST API — send messages, retrieve history, manage conversations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Query

from laya.db.sqlite import get_db
from laya.models.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Conversation,
    CreateConversationRequest,
)
from laya.pipeline.chat import canonical_card_ids, process_chat_message

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------


@router.post("/chat")
async def send_chat_message(body: ChatRequest) -> ChatResponse:
    """Send a chat message and receive an AI response."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        response = await process_chat_message(
            body.message.strip(),
            space_id=body.space_id,
            conversation_id=body.conversation_id,
            card_context=body.card_context,
            card_ids=body.card_ids,
        )
        return response
    except Exception as e:
        log.error("chat_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Chat processing failed")


@router.get("/chat/history")
async def get_chat_history(
    limit: int = 50,
    before: str | None = None,
    conversation_id: str | None = None,
) -> list[ChatMessage]:
    """Get chat message history, newest first."""
    db = await get_db()

    conditions = []
    params: list = []

    if conversation_id:
        conditions.append("conversation_id = ?")
        params.append(conversation_id)
    if before:
        conditions.append("timestamp < ?")
        params.append(before)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = await db.execute_fetchall(
        f"""SELECT message_id, timestamp, role, content,
                   referenced_cards, referenced_events, conversation_id
            FROM chat_messages
            {where}
            ORDER BY timestamp DESC
            LIMIT ?""",
        (*params, limit),
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
                conversation_id=row["conversation_id"],
            )
        )

    return messages


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@router.get("/chat/conversations")
async def list_conversations(limit: int = 50) -> list[Conversation]:
    """List all conversations ordered by most recently updated."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT c.conversation_id, c.title, c.space_id, c.created_at, c.updated_at,
                  (SELECT content FROM chat_messages m
                   WHERE m.conversation_id = c.conversation_id
                   ORDER BY m.timestamp DESC LIMIT 1) AS last_content,
                  (SELECT COUNT(*) FROM chat_messages m
                   WHERE m.conversation_id = c.conversation_id) AS message_count
           FROM chat_conversations c
           ORDER BY c.updated_at DESC
           LIMIT ?""",
        (limit,),
    )

    return [
        Conversation(
            conversation_id=row["conversation_id"],
            title=row["title"],
            space_id=row["space_id"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            preview=(row["last_content"] or "")[:100],
            message_count=row["message_count"] or 0,
        )
        for row in rows
    ]


@router.post("/chat/conversations")
async def create_conversation(body: CreateConversationRequest) -> Conversation:
    """Create a new chat conversation."""
    db = await get_db()
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO chat_conversations (conversation_id, title, space_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (conv_id, body.title, body.space_id, now, now),
    )
    await db.commit()

    return Conversation(
        conversation_id=conv_id,
        title=body.title,
        space_id=body.space_id,
        created_at=now,
        updated_at=now,
    )


@router.get("/chat/conversations/by-cards")
async def get_conversation_by_cards(
    card_ids: list[str] = Query(default=[]),
) -> Conversation | None:
    """Return the most recent conversation anchored to the given card set, or null.

    Used by Omni → View Cards to restore the prior chat when the user
    returns with the same card IDs. Match is on the canonical sorted JSON
    form so viewing order doesn't matter.
    """
    canonical = canonical_card_ids(card_ids)
    if not canonical:
        return None

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT c.conversation_id, c.title, c.space_id, c.created_at, c.updated_at,
                  (SELECT content FROM chat_messages m
                   WHERE m.conversation_id = c.conversation_id
                   ORDER BY m.timestamp DESC LIMIT 1) AS last_content,
                  (SELECT COUNT(*) FROM chat_messages m
                   WHERE m.conversation_id = c.conversation_id) AS message_count
           FROM chat_conversations c
           WHERE c.card_ids = ?
           ORDER BY c.updated_at DESC
           LIMIT 1""",
        (canonical,),
    )
    if not rows:
        return None

    row = rows[0]
    return Conversation(
        conversation_id=row["conversation_id"],
        title=row["title"],
        space_id=row["space_id"],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        preview=(row["last_content"] or "")[:100],
        message_count=row["message_count"] or 0,
    )


@router.get("/chat/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    limit: int = 50,
    before: str | None = None,
) -> list[ChatMessage]:
    """Get messages for a specific conversation."""
    db = await get_db()

    if before:
        rows = await db.execute_fetchall(
            """SELECT message_id, timestamp, role, content,
                      referenced_cards, referenced_events, conversation_id
               FROM chat_messages
               WHERE conversation_id = ? AND timestamp < ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (conversation_id, before, limit),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT message_id, timestamp, role, content,
                      referenced_cards, referenced_events, conversation_id
               FROM chat_messages
               WHERE conversation_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (conversation_id, limit),
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
                conversation_id=row["conversation_id"],
            )
        )

    return messages


@router.delete("/chat/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    """Delete a conversation and all its messages."""
    db = await get_db()

    # Delete messages first (FK CASCADE may not be enabled in SQLite by default)
    await db.execute(
        "DELETE FROM chat_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    result = await db.execute(
        "DELETE FROM chat_conversations WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conversation_id}


@router.put("/chat/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: dict) -> dict:
    """Update a conversation (e.g. rename)."""
    db = await get_db()
    title = body.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    now = datetime.now(timezone.utc).isoformat()
    result = await db.execute(
        "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
        (title, now, conversation_id),
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "updated", "conversation_id": conversation_id}
