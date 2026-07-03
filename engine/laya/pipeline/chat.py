# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Chat processing pipeline — hybrid retrieval, RRF, context packing, tool loop, LLM response."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from laya.db.chromadb_store import memory_search
from laya.db.fts import build_fts_match, fts_ready
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import llm_call, llm_call_streaming, StreamEvent
from laya.llm.prompts.chat import build_chat_messages, build_title_generation_messages
from laya.llm.tools.definitions import get_all_tool_definitions
from laya.llm.tools.executor import execute_tool
from laya.models.chat import ChatMessage, ChatResponse
from laya.tasks import create_task

log = structlog.get_logger()

# Pattern to extract card and event references from LLM output
CARD_REF_PATTERN = re.compile(r"\[card:([^\]]+)\]")
EVENT_REF_PATTERN = re.compile(r"\[event:([^\]]+)\]")

MAX_TOOL_ITERATIONS = 20

# Common English stopwords to skip in keyword search
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "am", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "it", "they", "them",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "how", "when", "where", "why", "and", "or", "but", "not", "no",
    "if", "then", "so", "to", "of", "in", "on", "at", "by", "for",
    "with", "about", "from", "up", "out", "into", "over", "after",
    "all", "any", "some", "just", "also", "than", "very", "too",
})


def canonical_card_ids(card_ids: list[str] | None) -> str | None:
    """Canonical JSON form for a card-ID set, used as the anchor key.

    Returns None when the input is empty so SQL IS NULL comparisons match
    non-card conversations. Duplicates are dropped and the order is stable so
    that viewing [A, B] and [B, A] map to the same conversation.
    """
    if not card_ids:
        return None
    cleaned = sorted({cid for cid in card_ids if cid})
    if not cleaned:
        return None
    return json.dumps(cleaned, separators=(",", ":"))


async def _ensure_conversation(
    db,
    user_message: str,
    conversation_id: str | None,
    space_id: str | None,
    card_ids: list[str] | None = None,
) -> str:
    """Return a valid conversation_id, creating one seeded with "New Chat" if needed.

    Title generation is triggered separately based on conversation state
    (see ``_should_generate_title``) so it works for both auto-created
    conversations and ones the UI pre-created via POST /chat/conversations.

    When ``card_ids`` is supplied and a new conversation is created, the
    canonical card-ID set is persisted so the conversation can later be
    looked up from the Omni → View Cards view.
    """
    if conversation_id:
        return conversation_id

    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    now = db_now()
    canonical = canonical_card_ids(card_ids)
    await db.execute(
        """INSERT INTO chat_conversations
           (conversation_id, title, space_id, created_at, updated_at, card_ids)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (conv_id, "New Chat", space_id, now, now, canonical),
    )
    await db.commit()
    return conv_id


# Reasoning models (Qwen 3, DeepSeek-R1, etc.) sometimes leak a preamble header
# as their first line of output — e.g. "Thinking Process:", "## Analysis". If
# that slips through the first-line sanitizer we don't want to persist it as a
# chat title. Keep this list aligned with the equivalent one in
# ``laya/api/egress_api.py::_clean_ai_draft``.
_REASONING_HEADER_RE = re.compile(
    r"^(?:#{1,3}\s*|\*{1,2})?"
    r"(?:thinking|thought|analysis|reasoning|approach|plan|evaluation|step)"
    r"(?:\s+process)?"
    r"\*{0,2}$",
    re.IGNORECASE,
)

# Strips any `<think>...</think>` block some providers (notably LM Studio
# serving Qwen 3) emit inline in ``content`` instead of splitting it into a
# separate ``reasoning_content`` field.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


async def _should_generate_title(db, conversation_id: str) -> bool:
    """True when a conversation still has the placeholder title and is recent enough to retry.

    Allows up to 3 retries (gated on assistant message count) so that a single
    failure from a reasoning model doesn't permanently leave the title as
    "New Chat".
    """
    rows = await db.execute_fetchall(
        """SELECT c.title,
                  (SELECT COUNT(*) FROM chat_messages m
                   WHERE m.conversation_id = c.conversation_id
                     AND m.role = 'assistant') AS assistant_msg_count
           FROM chat_conversations c
           WHERE c.conversation_id = ?""",
        (conversation_id,),
    )
    if not rows:
        return False
    return rows[0]["title"] == "New Chat" and (rows[0]["assistant_msg_count"] or 0) <= 3


def _sanitize_generated_title(raw: str) -> str:
    """Clean up LLM-generated title: strip quotes/punctuation, cap length.

    Returns "" when the cleaned text looks like a reasoning-model preamble
    header (e.g. "Thinking Process", "## Analysis"); the caller treats empty
    as "skip update" rather than persisting garbage.
    """
    # Remove any `<think>...</think>` block that leaked into ``content``
    # before doing any other processing.
    title = _THINK_BLOCK_RE.sub("", raw or "").strip()
    # Strip surrounding quotes that models sometimes add
    if len(title) >= 2 and title[0] in "\"'`" and title[-1] in "\"'`":
        title = title[1:-1].strip()
    # Drop a leading "Title:" prefix if the model ignored the instruction
    for prefix in ("Title:", "title:", "TITLE:"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
            break
    # Take the last non-empty line — reasoning models (regardless of vendor)
    # typically think first and output the answer on the final line.
    lines = [ln.strip() for ln in title.splitlines() if ln.strip()] if title else []
    title = lines[-1] if lines else ""
    title = title.rstrip(".!?,;: ")
    if len(title) > 50:
        title = title[:50].rstrip()
    # Guard: reasoning preambles ("Thinking Process", "Analysis", ...) are not
    # valid titles. Returning "" keeps the conversation as "New Chat".
    if title and _REASONING_HEADER_RE.match(title):
        return ""
    return title


async def _generate_title_background(
    conversation_id: str,
    user_message: str,
    space_id: str | None,
) -> None:
    """Generate a short title via the cheap router model and update the DB.

    Runs fire-and-forget so it never blocks the user's chat response. All
    failures are swallowed so a flaky router model cannot break chat.

    The token budget here has to accommodate thinking/reasoning models
    (Qwen 3, DeepSeek-R1, etc.) whose reasoning phase can be thousands of
    tokens before the final 2-6 word title is emitted. We try 2048 first and
    retry once at 4096 if the response was truncated; if it's *still*
    truncated we bail out and leave the conversation as "New Chat" rather
    than persist whatever reasoning text leaked through.
    """
    try:
        messages = build_title_generation_messages(user_message)
        response = await llm_call(
            role="router",
            messages=messages,
            step="chat_title",
            temperature=0.3,
            max_tokens=8192,
            space_id=space_id,
        )
        if response.finish_reason == "length":
            response = await llm_call(
                role="router",
                messages=messages,
                step="chat_title",
                temperature=0.3,
                max_tokens=16384,
                space_id=space_id,
            )
        if response.finish_reason == "length":
            log.info(
                "chat_title_generation_truncated",
                conversation_id=conversation_id,
                model=response.model,
                finish_reason=response.finish_reason,
            )
            return

        title = _sanitize_generated_title(response.content)
        if not title or title.lower() == "new chat":
            return

        now = db_now()
        db = await get_db()
        # Only overwrite if the title is still the placeholder — preserves any
        # manual rename the user may have made while generation was in flight.
        result = await db.execute(
            """UPDATE chat_conversations
               SET title = ?, updated_at = ?
               WHERE conversation_id = ? AND title = 'New Chat'""",
            (title, now, conversation_id),
        )
        await db.commit()
        if result.rowcount == 0:
            return

        # Notify any connected UI clients so the sidebar updates without
        # needing to re-fetch the full conversation list.
        try:
            from laya.api.websocket import manager
            await manager.broadcast({
                "type": "conversation_title_updated",
                "conversation_id": conversation_id,
                "title": title,
            })
        except Exception as exc:
            log.debug("chat_title_broadcast_failed", error=str(exc))

        log.info("chat_title_generated", conversation_id=conversation_id, title=title)
    except Exception as exc:
        log.warning(
            "chat_title_generation_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )


async def process_chat_message(
    user_message: str,
    space_id: str | None = None,
    conversation_id: str | None = None,
    card_context: str | None = None,
    card_ids: list[str] | None = None,
) -> ChatResponse:
    """Process a user chat message through the enhanced pipeline.

    Steps:
        1. Hybrid context retrieval (semantic + keyword + RRF)
        2. Load conversation history
        3. Generate response with tool loop (up to MAX_TOOL_ITERATIONS)

    Returns:
        ChatResponse with assistant message and references.
    """
    db = await get_db()
    conversation_id = await _ensure_conversation(
        db, user_message, conversation_id, space_id, card_ids=card_ids,
    )

    # Kick off title generation in parallel for first-message conversations so
    # it doesn't delay the reply. Runs against the cheap router model and
    # updates the DB + UI when done.
    if await _should_generate_title(db, conversation_id):
        create_task(
            _generate_title_background(conversation_id, user_message, space_id),
            name=f"chat_title_{conversation_id}",
        )

    # Skip broad retrieval when card_context is provided — the injected card
    # context already contains everything the LLM needs, and retrieval would
    # pull in unrelated cards/events that dilute the focused context.
    if card_context:
        context = {"context_text": "", "result_count": 0, "signals_used": []}
    else:
        context = await _retrieve_context(user_message, space_id=space_id)

    # Load recent chat history (scoped to conversation). timestamp has only
    # 1-second resolution, so a user+assistant pair written in the same second
    # could otherwise reorder — rowid DESC is the insertion-order tiebreaker
    # (review §4 — P5-8).
    history_rows = await db.execute_fetchall(
        """SELECT role, content FROM chat_messages
           WHERE conversation_id = ?
           ORDER BY timestamp DESC, rowid DESC LIMIT 10""",
        (conversation_id,),
    )
    chat_history = [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(history_rows)
    ]

    # Generate response with tool loop
    from laya.config import get_self_user
    user_identity = get_self_user()
    messages = build_chat_messages(
        user_message,
        chat_history,
        context_text=context["context_text"],
        user_identity=user_identity,
        card_context=card_context,
    )

    tools = get_all_tool_definitions()
    tool_calls_log: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0

    try:
        for iteration in range(MAX_TOOL_ITERATIONS + 1):
            response = await llm_call(
                role="chat",
                messages=messages,
                step="chat",
                temperature=0.3,
                max_tokens=65536,
                space_id=space_id,
                tools=tools if iteration < MAX_TOOL_ITERATIONS else None,
            )

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            total_latency_ms += response.latency_ms

            # If no tool calls, we're done
            if not response.tool_calls:
                break

            # Process tool calls
            log.info(
                "chat_tool_calls",
                iteration=iteration + 1,
                tools=[tc.name for tc in response.tool_calls],
            )

            # Append the assistant message with tool calls
            messages.append(response.raw_message_dict)

            # Execute each tool and append results
            for tc in response.tool_calls:
                result_str = await execute_tool(
                    tc.name, tc.arguments, space_id=space_id,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
                tool_calls_log.append({
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "result_preview": result_str[:200],
                })

        assistant_content = response.content
        model_used = response.model
    except Exception as e:
        log.error("chat_llm_failed", error=str(e))
        assistant_content = "I'm sorry, I encountered an error processing your message. Please try again."
        model_used = None
        total_input_tokens = 0
        total_output_tokens = 0
        total_latency_ms = 0

    # Extract card and event references
    referenced_cards = CARD_REF_PATTERN.findall(assistant_content)
    referenced_events = EVENT_REF_PATTERN.findall(assistant_content)

    # Store user message
    user_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = db_now()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content, space_id, conversation_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_msg_id, now, "user", user_message, space_id, conversation_id),
    )

    # Store assistant message
    assistant_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    assistant_ts = db_now()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content, referenced_cards,
            referenced_events, context_used, model_used, input_tokens,
            output_tokens, latency_ms, tool_calls_json, space_id, conversation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assistant_msg_id,
            assistant_ts,
            "assistant",
            assistant_content,
            json.dumps(referenced_cards) if referenced_cards else None,
            json.dumps(referenced_events) if referenced_events else None,
            json.dumps({
                "result_count": context["result_count"],
                "signals_used": context["signals_used"],
            }),
            model_used,
            total_input_tokens,
            total_output_tokens,
            total_latency_ms,
            json.dumps(tool_calls_log) if tool_calls_log else None,
            space_id,
            conversation_id,
        ),
    )
    # Update conversation timestamp
    await db.execute(
        "UPDATE chat_conversations SET updated_at = ? WHERE conversation_id = ?",
        (assistant_ts, conversation_id),
    )
    await db.commit()

    if tool_calls_log:
        log.info(
            "chat_tools_used",
            tool_count=len(tool_calls_log),
            tools=[t["name"] for t in tool_calls_log],
        )

    assistant_message = ChatMessage(
        message_id=assistant_msg_id,
        timestamp=assistant_ts,
        role="assistant",
        content=assistant_content,
        referenced_cards=referenced_cards,
        referenced_events=referenced_events,
        conversation_id=conversation_id,
    )

    return ChatResponse(
        message=assistant_message,
        referenced_cards=referenced_cards,
        referenced_events=referenced_events,
    )


async def process_chat_message_streaming(
    user_message: str,
    space_id: str | None = None,
    conversation_id: str | None = None,
    card_context: str | None = None,
    card_ids: list[str] | None = None,
):
    """Streaming version of process_chat_message.

    Yields dicts suitable for WebSocket broadcast:
        {"type": "chat_stream_start", "message_id": "...", "conversation_id": "..."}
        {"type": "chat_stream_chunk", "content": "..."}
        {"type": "chat_stream_tool", "tool": "...", "status": "calling"|"done"}
        {"type": "chat_stream_done", "message": {...}}

    The full message is persisted to DB after streaming completes.
    """
    db = await get_db()
    conversation_id = await _ensure_conversation(
        db, user_message, conversation_id, space_id, card_ids=card_ids,
    )

    if await _should_generate_title(db, conversation_id):
        create_task(
            _generate_title_background(conversation_id, user_message, space_id),
            name=f"chat_title_{conversation_id}",
        )

    # Skip broad retrieval when card_context is provided — the injected card
    # context already contains everything the LLM needs.
    if card_context:
        context = {"context_text": "", "result_count": 0, "signals_used": []}
    else:
        context = await _retrieve_context(user_message, space_id=space_id)

    # Chat history (scoped to conversation)
    history_rows = await db.execute_fetchall(
        """SELECT role, content FROM chat_messages
           WHERE conversation_id = ?
           ORDER BY timestamp DESC LIMIT 10""",
        (conversation_id,),
    )
    chat_history = [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(history_rows)
    ]

    from laya.config import get_self_user
    user_identity = get_self_user()
    messages = build_chat_messages(
        user_message,
        chat_history,
        context_text=context["context_text"],
        user_identity=user_identity,
        card_context=card_context,
    )

    tools = get_all_tool_definitions()
    tool_calls_log: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0
    full_content = ""
    model_used: str | None = None

    # Generate message IDs up front
    assistant_msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    yield {"type": "chat_stream_start", "message_id": assistant_msg_id, "conversation_id": conversation_id}

    # Store user message immediately
    user_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = db_now()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content, space_id, conversation_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_msg_id, now, "user", user_message, space_id, conversation_id),
    )
    await db.commit()

    try:
        for iteration in range(MAX_TOOL_ITERATIONS + 1):
            use_tools = tools if iteration < MAX_TOOL_ITERATIONS else None
            had_tool_calls = False

            async for event in llm_call_streaming(
                role="chat",
                messages=messages,
                step="chat",
                temperature=0.3,
                max_tokens=65536,
                space_id=space_id,
                tools=use_tools,
            ):
                if event.type == "chunk":
                    full_content += event.content
                    yield {"type": "chat_stream_chunk", "content": event.content}

                elif event.type == "tool_calls" and event.tool_calls:
                    had_tool_calls = True

                    # Notify UI about tool execution
                    for tc in event.tool_calls:
                        yield {
                            "type": "chat_stream_tool",
                            "tool": tc.name,
                            "status": "calling",
                        }

                    # Append assistant message with tool calls to conversation
                    messages.append(event.raw_message_dict)

                    # Execute tools
                    for tc in event.tool_calls:
                        result_str = await execute_tool(
                            tc.name, tc.arguments, space_id=space_id,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        })
                        tool_calls_log.append({
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "result_preview": result_str[:200],
                        })
                        yield {
                            "type": "chat_stream_tool",
                            "tool": tc.name,
                            "status": "done",
                        }

                    # Content accumulates across iterations so the final
                    # persisted message contains all streamed text.

                elif event.type == "done":
                    total_input_tokens += event.input_tokens
                    total_output_tokens += event.output_tokens
                    total_latency_ms += event.latency_ms
                    model_used = event.model

                elif event.type == "error":
                    full_content = "I'm sorry, I encountered an error processing your message. Please try again."
                    model_used = event.model
                    break

            # If no tool calls this iteration, we have the final response
            if not had_tool_calls:
                break

    except Exception as e:
        log.error("chat_stream_failed", error=str(e))
        full_content = "I'm sorry, I encountered an error processing your message. Please try again."
        model_used = None

    # Extract references
    referenced_cards = CARD_REF_PATTERN.findall(full_content)
    referenced_events = EVENT_REF_PATTERN.findall(full_content)

    # Store assistant message
    assistant_ts = db_now()
    await db.execute(
        """INSERT INTO chat_messages
           (message_id, timestamp, role, content, referenced_cards,
            referenced_events, context_used, model_used, input_tokens,
            output_tokens, latency_ms, tool_calls_json, space_id, conversation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assistant_msg_id,
            assistant_ts,
            "assistant",
            full_content,
            json.dumps(referenced_cards) if referenced_cards else None,
            json.dumps(referenced_events) if referenced_events else None,
            json.dumps({
                "result_count": context["result_count"],
                "signals_used": context["signals_used"],
            }),
            model_used,
            total_input_tokens,
            total_output_tokens,
            total_latency_ms,
            json.dumps(tool_calls_log) if tool_calls_log else None,
            space_id,
            conversation_id,
        ),
    )
    # Update conversation timestamp
    await db.execute(
        "UPDATE chat_conversations SET updated_at = ? WHERE conversation_id = ?",
        (assistant_ts, conversation_id),
    )
    await db.commit()

    # Final done event with the complete message
    yield {
        "type": "chat_stream_done",
        "message": {
            "message_id": assistant_msg_id,
            "timestamp": assistant_ts,
            "role": "assistant",
            "content": full_content,
            "referenced_cards": referenced_cards,
            "referenced_events": referenced_events,
            "conversation_id": conversation_id,
        },
    }


# ---------------------------------------------------------------------------
# Hybrid Retrieval
# ---------------------------------------------------------------------------


async def _retrieve_context(
    user_message: str,
    space_id: str | None = None,
    token_budget: int = 3000,
) -> dict[str, Any]:
    """Multi-signal retrieval with Reciprocal Rank Fusion.

    Runs semantic search, card keyword search, event keyword search,
    and entity search in parallel, then fuses results using RRF.
    """
    # Run all retrievers in parallel
    results = await asyncio.gather(
        _semantic_search(user_message, space_id, n=10),
        _card_keyword_search(user_message, space_id, n=10),
        _event_keyword_search(user_message, space_id, n=10),
        _entity_search(user_message, n=5),
        return_exceptions=True,
    )

    # Collect successful results
    ranked_lists: list[list[dict]] = []
    for r in results:
        if isinstance(r, list):
            ranked_lists.append(r)
        elif isinstance(r, Exception):
            log.warning("retrieval_signal_failed", error=str(r))

    # Reciprocal Rank Fusion
    fused = _reciprocal_rank_fusion(ranked_lists, k=60)

    # Deduplicate by source ID
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in fused:
        uid = item.get("id") or item.get("card_id") or item.get("event_id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            deduped.append(item)

    # Pack into context string within token budget
    context_text = _pack_context(deduped[:12], token_budget)

    return {
        "context_text": context_text,
        "result_count": len(deduped),
        "signals_used": len(ranked_lists),
        "items": deduped[:12],
    }


async def _semantic_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """ChromaDB semantic search."""
    where = {"space_id": space_id} if space_id else None
    results = await memory_search(query, n_results=n, where=where, max_distance=0.60)
    return [
        {
            "id": r["metadata"].get("card_id", r["metadata"].get("event_id", r["id"])),
            "card_id": r["metadata"].get("card_id"),
            "event_id": r["metadata"].get("event_id"),
            "type": r["metadata"].get("content_type", "memory"),
            "text": r["document"],
            "metadata": r["metadata"],
            "source": "semantic",
        }
        for r in results
    ]


async def _card_keyword_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """Keyword search on cards — FTS5/BM25 when available, else SQL LIKE."""
    match = build_fts_match(query, min_len=3, max_terms=8)
    if fts_ready() and match:
        try:
            return await _card_keyword_search_fts(match, space_id, n)
        except Exception as e:
            log.warning("cards_fts_failed_fallback_like", error=str(e))
    return await _card_keyword_search_like(query, space_id, n)


async def _card_keyword_search_fts(match: str, space_id: str | None, n: int) -> list[dict]:
    """BM25-ranked card search over the cards_fts index."""
    db = await get_db()
    where = "cards_fts MATCH ?"
    params: list = [match]
    if space_id:
        where += " AND c.space_id = ?"
        params.append(space_id)
    params.append(n)

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.header, c.summary, c.status, c.priority, c.persona, c.space_id
            FROM cards_fts
            JOIN action_cards c ON c.card_id = cards_fts.card_id
            WHERE {where}
            ORDER BY bm25(cards_fts) LIMIT ?""",
        params,
    )
    return _format_card_keyword_rows(rows)


async def _card_keyword_search_like(query: str, space_id: str | None, n: int) -> list[dict]:
    """SQLite LIKE keyword search on cards (fallback when FTS5 is unavailable)."""
    db = await get_db()
    keywords = [w for w in query.split() if len(w) >= 3 and w.lower() not in _STOPWORDS]
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:8]:
        conditions.append("(header LIKE ? OR summary LIKE ? OR intelligence LIKE ?)")
        params.extend([f"%{kw}%"] * 3)

    where = " OR ".join(conditions)
    if space_id:
        where = f"({where}) AND space_id = ?"
        params.append(space_id)

    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT card_id, header, summary, status, priority, persona, space_id
            FROM action_cards WHERE {where}
            ORDER BY created_at DESC LIMIT ?""",
        params,
    )
    return _format_card_keyword_rows(rows)


def _format_card_keyword_rows(rows) -> list[dict]:
    """Shape card rows (from either FTS or LIKE search) into result dicts."""
    return [
        {
            "id": row["card_id"],
            "card_id": row["card_id"],
            "type": "card",
            "text": f"[{row['priority']}] {row['header']}: {row['summary']}",
            "metadata": {
                "status": row["status"],
                "priority": row["priority"],
                "persona": row["persona"],
                "space_id": row["space_id"],
            },
            "source": "keyword",
        }
        for row in rows
    ]


async def _event_keyword_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """Keyword search on events — FTS5/BM25 when available, else SQL LIKE."""
    match = build_fts_match(query, min_len=3, max_terms=5)
    if fts_ready() and match:
        try:
            return await _event_keyword_search_fts(match, space_id, n)
        except Exception as e:
            log.warning("events_fts_failed_fallback_like", error=str(e))
    return await _event_keyword_search_like(query, space_id, n)


async def _event_keyword_search_fts(match: str, space_id: str | None, n: int) -> list[dict]:
    """BM25-ranked event search over the events_fts index."""
    db = await get_db()
    where = "events_fts MATCH ?"
    params: list = [match]
    if space_id:
        where += " AND e.space_id = ?"
        params.append(space_id)
    params.append(n)

    rows = await db.execute_fetchall(
        f"""SELECT e.event_id, e.source_platform, e.subject_title, e.timestamp, e.space_id
            FROM events_fts
            JOIN events e ON e.event_id = events_fts.event_id
            WHERE {where}
            ORDER BY bm25(events_fts) LIMIT ?""",
        params,
    )
    return _format_event_keyword_rows(rows)


async def _event_keyword_search_like(query: str, space_id: str | None, n: int) -> list[dict]:
    """SQLite LIKE keyword search on events (fallback when FTS5 is unavailable)."""
    db = await get_db()
    keywords = [w for w in query.split() if len(w) >= 3 and w.lower() not in _STOPWORDS]
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:5]:
        conditions.append("(subject_title LIKE ? OR content_body LIKE ?)")
        params.extend([f"%{kw}%"] * 2)

    where = " OR ".join(conditions)
    if space_id:
        where = f"({where}) AND space_id = ?"
        params.append(space_id)

    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT event_id, source_platform, subject_title, timestamp, space_id
            FROM events WHERE {where}
            ORDER BY timestamp DESC LIMIT ?""",
        params,
    )
    return _format_event_keyword_rows(rows)


def _format_event_keyword_rows(rows) -> list[dict]:
    """Shape event rows (from either FTS or LIKE search) into result dicts."""
    return [
        {
            "id": row["event_id"],
            "event_id": row["event_id"],
            "type": "event",
            "text": f"[{row['source_platform']}] {row['subject_title']}",
            "metadata": {
                "source_platform": row["source_platform"],
                "timestamp": row["timestamp"],
                "space_id": row["space_id"],
            },
            "source": "keyword",
        }
        for row in rows
    ]


async def _entity_search(query: str, n: int) -> list[dict]:
    """Search entities table for cross-platform correlations."""
    db = await get_db()
    keywords = [w for w in query.split() if len(w) >= 3 and w.lower() not in _STOPWORDS]
    if not keywords:
        return []

    conditions = []
    params = []
    for kw in keywords[:5]:
        conditions.append("canonical_name LIKE ?")
        params.append(f"%{kw}%")

    where = " OR ".join(conditions)
    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT entity_id, entity_type, canonical_name, platform_refs, confidence
            FROM entities WHERE {where}
            ORDER BY confidence DESC LIMIT ?""",
        params,
    )

    return [
        {
            "id": row["entity_id"],
            "type": "entity",
            "text": f"[{row['entity_type']}] {row['canonical_name']}",
            "metadata": {
                "entity_type": row["entity_type"],
                "platform_refs": row["platform_refs"],
                "confidence": row["confidence"],
            },
            "source": "entity",
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def _reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    k: int = 60,
) -> list[dict]:
    """Fuse multiple ranked lists using RRF.

    score(d) = Σ 1/(k + rank_i(d)) across all lists that contain d.
    Higher k reduces the impact of high rankings in individual lists.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            doc_id = item.get("id") or item.get("card_id") or item.get("event_id") or str(rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in items:
                items[doc_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
    return [items[did] for did in sorted_ids]


# ---------------------------------------------------------------------------
# Context Packing
# ---------------------------------------------------------------------------


def _pack_context(items: list[dict], token_budget: int = 3000) -> str:
    """Pack retrieved items into a context string within token budget.

    Higher-ranked items get priority. Each item is formatted with its type
    and key metadata. Approximate tokens as len(text) / 4.
    """
    parts: list[str] = []
    used_tokens = 0

    for item in items:
        formatted = _format_context_item(item)
        est_tokens = len(formatted) // 4

        if used_tokens + est_tokens > token_budget:
            # Try truncated version
            remaining_chars = (token_budget - used_tokens) * 4
            if remaining_chars > 200:
                parts.append(formatted[:remaining_chars] + "...")
            break

        parts.append(formatted)
        used_tokens += est_tokens

    return "\n\n".join(parts) if parts else ""


def _format_context_item(item: dict) -> str:
    """Format a single retrieval result for context injection."""
    itype = item.get("type", "unknown")
    meta = item.get("metadata", {})
    text = item.get("text", "")

    if itype in ("card", "card_summary"):
        card_id = item.get("card_id") or meta.get("card_id") or item.get("id", "?")
        status = meta.get("status", "?")
        priority = meta.get("priority", "?")
        return f"[Card {card_id}] ({status}/{priority}) {text}"
    elif itype == "event":
        event_id = item.get("event_id", item.get("id", "?"))
        platform = meta.get("source_platform", "?")
        return f"[Event {event_id}] [{platform}] {text}"
    elif itype == "entity":
        etype = meta.get("entity_type", "?")
        return f"[Entity:{etype}] {text}"
    else:
        platform = meta.get("source_platform", "")
        prefix = f"[{platform}] " if platform else ""
        return f"{prefix}{text[:500]}"
