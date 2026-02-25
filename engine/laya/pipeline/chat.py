"""Chat processing pipeline — parse intent, retrieve context, generate response."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.chat import build_chat_messages
from laya.models.chat import ChatMessage, ChatResponse

log = structlog.get_logger()

# Pattern to extract card and event references from LLM output
CARD_REF_PATTERN = re.compile(r"\[card:([^\]]+)\]")
EVENT_REF_PATTERN = re.compile(r"\[event:([^\]]+)\]")


async def process_chat_message(user_message: str) -> ChatResponse:
    """Process a user chat message through the 3-step pipeline.

    Steps:
        1. Retrieve context (ChromaDB + SQLite)
        2. Load conversation history
        3. Generate response (LLM with context)

    Returns:
        ChatResponse with assistant message and references.
    """
    db = await get_db()

    # Step 1: Retrieve context
    context = await _retrieve_context(user_message)

    # Step 2: Load recent chat history
    history_rows = await db.execute_fetchall(
        """SELECT role, content FROM chat_messages
           ORDER BY timestamp DESC LIMIT 10"""
    )
    chat_history = [
        {"role": row[0], "content": row[1]}
        for row in reversed(history_rows)
    ]

    # Step 3: Generate response via LLM
    messages = build_chat_messages(user_message, chat_history, context)

    try:
        response = await llm_call(
            role="chat",
            messages=messages,
            step="chat",
            temperature=0.3,
            max_tokens=2000,
        )
        assistant_content = response.content
        model_used = response.model
        input_tokens = response.input_tokens
        output_tokens = response.output_tokens
        latency_ms = response.latency_ms
    except Exception as e:
        log.error("chat_llm_failed", error=str(e))
        assistant_content = "I'm sorry, I encountered an error processing your message. Please try again."
        model_used = None
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0

    # Extract card and event references
    referenced_cards = CARD_REF_PATTERN.findall(assistant_content)
    referenced_events = EVENT_REF_PATTERN.findall(assistant_content)

    # Store user message
    user_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content)
           VALUES (?, ?, ?, ?)""",
        (user_msg_id, now, "user", user_message),
    )

    # Store assistant message
    assistant_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    assistant_ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content, referenced_cards,
            referenced_events, context_used, model_used, input_tokens,
            output_tokens, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assistant_msg_id,
            assistant_ts,
            "assistant",
            assistant_content,
            json.dumps(referenced_cards) if referenced_cards else None,
            json.dumps(referenced_events) if referenced_events else None,
            json.dumps({"card_count": len(context.get("related_cards", [])),
                        "event_count": len(context.get("related_events", [])),
                        "memory_count": len(context.get("memory_results", []))}),
            model_used,
            input_tokens,
            output_tokens,
            latency_ms,
        ),
    )
    await db.commit()

    assistant_message = ChatMessage(
        message_id=assistant_msg_id,
        timestamp=assistant_ts,
        role="assistant",
        content=assistant_content,
        referenced_cards=referenced_cards,
        referenced_events=referenced_events,
    )

    return ChatResponse(
        message=assistant_message,
        referenced_cards=referenced_cards,
        referenced_events=referenced_events,
    )


async def _retrieve_context(user_message: str) -> dict:
    """Retrieve relevant context for the chat response.

    Searches ChromaDB for semantic matches and SQLite for cards/events.
    """
    db = await get_db()
    context: dict = {
        "related_cards": [],
        "related_events": [],
        "memory_results": [],
    }

    # ChromaDB semantic search
    try:
        memory_results = await memory_search(user_message, n_results=5)
        context["memory_results"] = memory_results
    except Exception as e:
        log.warning("chat_memory_search_failed", error=str(e))

    # Search cards by keyword matching in header/summary
    keywords = user_message.split()[:5]  # Use first 5 words as keywords
    if keywords:
        like_clause = " OR ".join(["header LIKE ? OR summary LIKE ?"] * len(keywords))
        params = []
        for kw in keywords:
            params.extend([f"%{kw}%", f"%{kw}%"])

        try:
            card_rows = await db.execute_fetchall(
                f"""SELECT card_id, header, summary, status, priority, persona
                    FROM action_cards
                    WHERE {like_clause}
                    ORDER BY created_at DESC
                    LIMIT 5""",
                params,
            )
            context["related_cards"] = [
                {
                    "card_id": row[0],
                    "header": row[1],
                    "summary": row[2],
                    "status": row[3],
                    "priority": row[4],
                    "persona": row[5],
                }
                for row in card_rows
            ]
        except Exception as e:
            log.warning("chat_card_search_failed", error=str(e))

    # Search events by subject title
    try:
        event_rows = await db.execute_fetchall(
            """SELECT event_id, source_platform, subject_title, timestamp
               FROM events
               WHERE subject_title LIKE ?
               ORDER BY timestamp DESC
               LIMIT 5""",
            (f"%{user_message[:50]}%",),
        )
        context["related_events"] = [
            {
                "event_id": row[0],
                "source_platform": row[1],
                "subject_title": row[2],
                "timestamp": row[3],
            }
            for row in event_rows
        ]
    except Exception as e:
        log.warning("chat_event_search_failed", error=str(e))

    return context
