# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Chat REST API and pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.llm.client import LLMResponse
from tests.conftest import insert_test_card, insert_test_event


def _mock_llm_response(content: str = "Here is my response about [card:card_123].") -> LLMResponse:
    return LLMResponse(
        content=content,
        parsed=None,
        model="anthropic/claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
        latency_ms=200,
    )


async def _create_conversation(db, conversation_id="conv_test01", space_id=None):
    """Create a conversation row and return its ID."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO chat_conversations (conversation_id, title, space_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (conversation_id, "Test Conversation", space_id, now, now),
    )
    await db.commit()
    return conversation_id


async def _insert_chat_message(db, message_id, role, content, conversation_id):
    """Insert a chat message with the required conversation_id."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO chat_messages (message_id, timestamp, role, content, conversation_id)
           VALUES (?, ?, ?, ?, ?)""",
        (message_id, now, role, content, conversation_id),
    )
    await db.commit()


@pytest.mark.asyncio
class TestChatAPI:
    async def test_send_chat_returns_response(self, db):
        """POST /chat returns a response with assistant message."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response()):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/chat", json={"message": "What happened today?"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"]["role"] == "assistant"
        assert len(data["message"]["content"]) > 0

    async def test_chat_stores_messages(self, db):
        """POST /chat stores both user and assistant messages in DB."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("Response text")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    await client.post("/chat", json={"message": "Hello"})

        rows = await db.execute_fetchall(
            "SELECT role, content FROM chat_messages ORDER BY timestamp ASC"
        )
        assert len(rows) == 2
        assert rows[0][0] == "user"
        assert rows[0][1] == "Hello"
        assert rows[1][0] == "assistant"
        assert rows[1][1] == "Response text"

    async def test_chat_history_endpoint(self, db):
        """GET /chat/history returns stored messages."""
        conv_id = await _create_conversation(db)
        await _insert_chat_message(db, "msg_1", "user", "Hello", conv_id)
        await _insert_chat_message(db, "msg_2", "assistant", "Hi there!", conv_id)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/chat/history")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_chat_history_limit(self, db):
        """GET /chat/history?limit=1 respects limit parameter."""
        conv_id = await _create_conversation(db)
        for i in range(5):
            await _insert_chat_message(db, f"msg_{i}", "user", f"Message {i}", conv_id)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/chat/history?limit=2")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_chat_extracts_card_references(self, db):
        """POST /chat extracts [card:ID] references from assistant response."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("Found [card:card_abc] and [card:card_def].")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/chat", json={"message": "Show me cards"})

        data = resp.json()
        assert "card_abc" in data["referenced_cards"]
        assert "card_def" in data["referenced_cards"]

    async def test_chat_extracts_event_references(self, db):
        """POST /chat extracts [event:ID] references from assistant response."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("See [event:evt_123] for details.")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/chat", json={"message": "Show events"})

        data = resp.json()
        assert "evt_123" in data["referenced_events"]

    async def test_chat_empty_message_rejected(self, db):
        """POST /chat rejects empty messages."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/chat", json={"message": ""})

        assert resp.status_code == 400

    async def test_chat_whitespace_message_rejected(self, db):
        """POST /chat rejects whitespace-only messages."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/chat", json={"message": "   "})

        assert resp.status_code == 400

    async def test_chat_llm_failure_returns_error_message(self, db):
        """POST /chat returns graceful error message when LLM fails."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   side_effect=Exception("LLM unavailable")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/chat", json={"message": "Hello"})

        # The pipeline catches the error and returns a fallback message
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data["message"]["content"].lower() or "sorry" in data["message"]["content"].lower()

    async def test_chat_context_retrieval_with_cards(self, db):
        """Chat retrieves related cards when keywords match."""
        await insert_test_event(db, "evt_ctx")
        await insert_test_card(db, "card_ctx", "evt_ctx")

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("Found related card.")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                from laya.main import app
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/chat", json={"message": "NPE PaymentService"})

        assert resp.status_code == 200

    async def test_chat_persists_card_ids(self, db):
        """Sending a chat with card_ids tags the new conversation with the canonical set."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("Response.")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
                    from laya.main import app
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.post(
                            "/chat",
                            json={"message": "Anchor me", "card_ids": ["card_b", "card_a"]},
                        )

        assert resp.status_code == 200
        conv_id = resp.json()["message"]["conversation_id"]
        rows = await db.execute_fetchall(
            "SELECT card_ids FROM chat_conversations WHERE conversation_id = ?",
            (conv_id,),
        )
        # Canonical form is sorted JSON
        assert rows[0]["card_ids"] == '["card_a","card_b"]'

    async def test_by_cards_lookup_returns_latest_conversation(self, db):
        """GET /chat/conversations/by-cards returns the conversation anchored to the cards."""
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=_mock_llm_response("Response.")):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
                    from laya.main import app
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        # First message anchors the conversation to [card_x, card_y]
                        first = await client.post(
                            "/chat",
                            json={"message": "First", "card_ids": ["card_x", "card_y"]},
                        )
                        conv_id = first.json()["message"]["conversation_id"]

                        # Lookup with the cards in reversed order should still match
                        lookup = await client.get(
                            "/chat/conversations/by-cards",
                            params=[("card_ids", "card_y"), ("card_ids", "card_x")],
                        )

        assert lookup.status_code == 200
        body = lookup.json()
        assert body is not None
        assert body["conversation_id"] == conv_id

    async def test_by_cards_lookup_returns_null_when_no_match(self, db):
        """by-cards lookup returns null when no conversation matches the card set."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/chat/conversations/by-cards",
                params=[("card_ids", "card_missing")],
            )
        assert resp.status_code == 200
        assert resp.json() is None


def _title_llm_response(content: str, finish_reason: str = "stop") -> LLMResponse:
    return LLMResponse(
        content=content,
        parsed=None,
        model="lmstudio/qwen/qwen3.6-35b-a3b",
        input_tokens=100,
        output_tokens=20,
        latency_ms=500,
        finish_reason=finish_reason,
    )


class TestTitleSanitizer:
    """Guards around ``_sanitize_generated_title`` — reasoning-preamble rejection."""

    def test_rejects_thinking_process_preamble(self):
        from laya.pipeline.chat import _sanitize_generated_title
        assert _sanitize_generated_title("Thinking Process:") == ""
        assert _sanitize_generated_title("Thought Process") == ""
        assert _sanitize_generated_title("## Analysis") == ""
        assert _sanitize_generated_title("**Reasoning**") == ""

    def test_keeps_real_titles(self):
        from laya.pipeline.chat import _sanitize_generated_title
        assert _sanitize_generated_title("Debugging Jira Webhook") == "Debugging Jira Webhook"
        assert _sanitize_generated_title('"Triage Overdue Jira"') == "Triage Overdue Jira"
        assert _sanitize_generated_title("Title: Triage Overdue Jira") == "Triage Overdue Jira"

    def test_strips_inline_think_block(self):
        from laya.pipeline.chat import _sanitize_generated_title
        raw = "<think>pondering the topic briefly</think>\nDebugging Jira Webhook"
        assert _sanitize_generated_title(raw) == "Debugging Jira Webhook"


@pytest.mark.asyncio
class TestTitleGenerationBackground:
    """End-to-end behavior of ``_generate_title_background`` with a mocked LLM."""

    async def _make_convo(self, db, conv_id="conv_title01"):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO chat_conversations
               (conversation_id, title, space_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conv_id, "New Chat", None, now, now),
        )
        await db.commit()
        return conv_id

    async def _get_title(self, db, conv_id):
        rows = await db.execute_fetchall(
            "SELECT title FROM chat_conversations WHERE conversation_id = ?",
            (conv_id,),
        )
        return rows[0]["title"]

    async def test_truncated_reasoning_does_not_overwrite_title(self, db):
        """If both attempts come back finish_reason='length', leave 'New Chat' in place.

        This is the bug: a Qwen-3/LM-Studio setup was returning reasoning text
        (starting with 'Thinking Process:') whenever the token budget was
        exhausted mid-thinking. That must never become a persisted title.
        """
        from laya.pipeline.chat import _generate_title_background

        conv_id = await self._make_convo(db)
        # Both calls (2048 and 4096 budget) truncate.
        truncated = _title_llm_response("", finish_reason="length")
        mock = AsyncMock(side_effect=[truncated, truncated])
        with patch("laya.pipeline.chat.llm_call", mock):
            await _generate_title_background(conv_id, "help me triage", None)

        assert await self._get_title(db, conv_id) == "New Chat"
        assert mock.await_count == 2
        # First attempt uses 8192; retry escalates to 16384.
        assert mock.await_args_list[0].kwargs["max_tokens"] == 8192
        assert mock.await_args_list[1].kwargs["max_tokens"] == 16384

    async def test_retry_succeeds_and_title_is_saved(self, db):
        """If the first call truncates but the retry returns a clean title, persist it."""
        from laya.pipeline.chat import _generate_title_background

        conv_id = await self._make_convo(db, "conv_title02")
        mock = AsyncMock(side_effect=[
            _title_llm_response("", finish_reason="length"),
            _title_llm_response("Triage Overdue Jira", finish_reason="stop"),
        ])
        with patch("laya.pipeline.chat.llm_call", mock):
            await _generate_title_background(conv_id, "help me triage", None)

        assert await self._get_title(db, conv_id) == "Triage Overdue Jira"
        assert mock.await_count == 2

    async def test_reasoning_preamble_in_content_is_not_saved(self, db):
        """If the sanitizer sees a reasoning header, drop it rather than persist.

        Models occasionally emit a header on the first line even when they
        finish cleanly. We must not let 'Thinking Process' become a title.
        """
        from laya.pipeline.chat import _generate_title_background

        conv_id = await self._make_convo(db, "conv_title03")
        response = _title_llm_response("Thinking Process:", finish_reason="stop")
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=response):
            await _generate_title_background(conv_id, "hi", None)

        assert await self._get_title(db, conv_id) == "New Chat"

    async def test_inline_think_block_is_stripped_before_saving(self, db):
        """`<think>...</think>` wrapper around reasoning should be stripped."""
        from laya.pipeline.chat import _generate_title_background

        conv_id = await self._make_convo(db, "conv_title04")
        response = _title_llm_response(
            "<think>user wants to triage jira tickets</think>\nTriage Jira Tickets",
            finish_reason="stop",
        )
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock,
                   return_value=response):
            with patch("laya.api.websocket.manager.broadcast",
                       new_callable=AsyncMock):
                await _generate_title_background(conv_id, "triage my jira", None)

        assert await self._get_title(db, conv_id) == "Triage Jira Tickets"


def test_cap_tool_result_passthrough_and_truncation():
    """P6-12: small results pass through; oversized ones truncate with a hint."""
    from laya.pipeline.chat import _cap_tool_result, _MAX_TOOL_RESULT_CHARS
    assert _cap_tool_result("tiny") == "tiny"
    big = "y" * (_MAX_TOOL_RESULT_CHARS * 3)
    out = _cap_tool_result(big)
    assert out.startswith("y" * _MAX_TOOL_RESULT_CHARS)
    assert len(out) < len(big)
    assert "truncated" in out and "offset" in out


@pytest.mark.asyncio
async def test_prev_turn_used_tools_signal(db):
    """P6-14: ambient retrieval is skipped when the last assistant turn used tools."""
    from laya.pipeline.chat import _prev_turn_used_tools

    await db.execute("INSERT INTO chat_conversations (conversation_id) VALUES ('conv_x')")
    await db.commit()

    async def _msg(mid, role, tools_json, conv="conv_x"):
        await db.execute(
            """INSERT INTO chat_messages (message_id, timestamp, role, content,
                   conversation_id, tool_calls_json)
               VALUES (?, datetime('now'), ?, 'hi', ?, ?)""",
            (mid, role, conv, tools_json),
        )
        await db.commit()

    # No conversation → False
    assert await _prev_turn_used_tools(None) is False
    # No messages yet → False
    assert await _prev_turn_used_tools("conv_x") is False
    # Last assistant turn had NO tools → False
    await _msg("m1", "user", None)
    await _msg("m2", "assistant", None)
    assert await _prev_turn_used_tools("conv_x") is False
    # Newer assistant turn USED tools → True
    await _msg("m3", "user", None)
    await _msg("m4", "assistant", '[{"name": "search_cards"}]')
    assert await _prev_turn_used_tools("conv_x") is True


@pytest.mark.asyncio
class TestStreamingPersistence:
    """The streaming pipeline persists the assistant row up front and salvages
    partial content when the stream is interrupted — an interrupted or hung
    stream used to lose the entire reply (it was only INSERTed at the end)."""

    async def _rows(self, db, conv_id, role):
        return await db.execute_fetchall(
            "SELECT message_id, content, model_used FROM chat_messages "
            "WHERE conversation_id = ? AND role = ? ORDER BY rowid",
            (conv_id, role),
        )

    async def test_completed_stream_finalizes_single_row(self, db):
        """Happy path: content lands in the pre-inserted row (no duplicate)."""
        import asyncio
        from laya.llm.client import StreamEvent
        from laya.pipeline.chat import process_chat_message_streaming

        conv_id = await _create_conversation(db, "conv_stream_ok")

        async def fake_stream(**kwargs):
            yield StreamEvent(type="chunk", content="Hello ")
            yield StreamEvent(type="chunk", content="world")
            yield StreamEvent(
                type="done", model="test-model",
                input_tokens=10, output_tokens=5, latency_ms=100,
            )

        events = []
        with patch("laya.pipeline.chat.llm_call_streaming", new=fake_stream), \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
            async for ev in process_chat_message_streaming("hi", conversation_id=conv_id):
                events.append(ev)

        assert events[0]["type"] == "chat_stream_start"
        assert events[-1]["type"] == "chat_stream_done"
        assert events[-1]["message"]["content"] == "Hello world"

        rows = await self._rows(db, conv_id, "assistant")
        assert len(rows) == 1
        assert rows[0]["content"] == "Hello world"
        assert rows[0]["model_used"] == "test-model"
        # The done event's id matches the persisted row (UI keys on it)
        assert rows[0]["message_id"] == events[-1]["message"]["message_id"]

    async def test_closed_generator_salvages_partial_content(self, db):
        """Consumer dies mid-stream (GeneratorExit) → partial reply survives
        with an interrupted marker; the user message survives too."""
        import asyncio
        from laya.llm.client import StreamEvent
        from laya.pipeline.chat import process_chat_message_streaming, _INTERRUPTED_MARKER

        conv_id = await _create_conversation(db, "conv_stream_cut")

        async def fake_stream(**kwargs):
            yield StreamEvent(type="chunk", content="Partial ")
            yield StreamEvent(type="chunk", content="answer")
            await asyncio.Event().wait()  # would hang forever
            yield StreamEvent(type="done")  # pragma: no cover

        with patch("laya.pipeline.chat.llm_call_streaming", new=fake_stream), \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
            gen = process_chat_message_streaming("continue", conversation_id=conv_id)
            assert (await anext(gen))["type"] == "chat_stream_start"
            assert (await anext(gen))["content"] == "Partial "
            assert (await anext(gen))["content"] == "answer"
            await gen.aclose()

        rows = await self._rows(db, conv_id, "assistant")
        assert len(rows) == 1
        assert rows[0]["content"] == "Partial answer" + _INTERRUPTED_MARKER

        user_rows = await self._rows(db, conv_id, "user")
        assert len(user_rows) == 1
        assert user_rows[0]["content"] == "continue"

    async def test_cancelled_task_with_no_content_deletes_placeholder(self, db):
        """Task cancelled (engine shutdown) before any chunk arrived → the empty
        placeholder row is removed so history gets no blank assistant bubble."""
        import asyncio
        from laya.llm.client import StreamEvent
        from laya.pipeline.chat import process_chat_message_streaming

        conv_id = await _create_conversation(db, "conv_stream_cancel")
        llm_entered = asyncio.Event()

        async def fake_stream(**kwargs):
            llm_entered.set()
            await asyncio.Event().wait()  # hang until cancelled
            yield StreamEvent(type="chunk", content="never")  # pragma: no cover

        async def consume():
            async for _ in process_chat_message_streaming("hi", conversation_id=conv_id):
                pass

        with patch("laya.pipeline.chat.llm_call_streaming", new=fake_stream), \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(llm_entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert await self._rows(db, conv_id, "assistant") == []
        # The user message still survives the cancellation
        user_rows = await self._rows(db, conv_id, "user")
        assert len(user_rows) == 1


@pytest.mark.asyncio
class TestChatDocuments:
    """Corpus documents in chat: citations, attachments, retrieval and prompt gating."""

    def _doc_hit(self):
        return {
            "id": "doc_a_chunk_0", "document": "The deadline is 2026-09-30.",
            "metadata": {"content_type": "document_chunk", "document_id": "doc_a", "title": "Roadmap",
                         "page_start": 3, "page_end": 3, "heading": "Dates", "chunk_index": 0},
            "distance": 0.2,
        }

    async def test_citations_persisted_rest(self, db):
        from tests.conftest import insert_test_document

        await insert_test_document(db, document_id="doc_a", title="Roadmap")
        reply = "Per [doc:doc_a p.3] and [doc:doc_a] the date is fixed; see [card:card_1]."
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=_mock_llm_response(reply)), \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
            async with AsyncClient(transport=ASGITransport(app=__import__("laya.main").main.app), base_url="http://test") as c:
                resp = await c.post("/chat", json={"message": "when is the deadline?"})
        body = resp.json()
        assert body["referenced_documents"] == ["doc_a"]
        assert body["message"]["referenced_documents"] == ["doc_a"]
        rows = await db.execute_fetchall("SELECT referenced_documents FROM chat_messages WHERE role = 'assistant'")
        assert json.loads(rows[0]["referenced_documents"]) == ["doc_a"]
        async with AsyncClient(transport=ASGITransport(app=__import__("laya.main").main.app), base_url="http://test") as c:
            history = (await c.get("/chat/history")).json()
        assert history[0]["referenced_documents"] == ["doc_a"]

    async def test_citations_persisted_streaming(self, db):
        from laya.llm.client import StreamEvent
        from laya.pipeline.chat import process_chat_message_streaming

        async def fake_stream(**kwargs):
            yield StreamEvent(type="chunk", content="See [doc:doc_z page 2].")
            yield StreamEvent(type="done", model="m")

        conv_id = await _create_conversation(db, "conv_doc_stream")
        events = []
        with patch("laya.pipeline.chat.llm_call_streaming", new=fake_stream), \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]):
            async for ev in process_chat_message_streaming("hi", conversation_id=conv_id):
                events.append(ev)
        assert events[-1]["message"]["referenced_documents"] == ["doc_z"]
        rows = await db.execute_fetchall("SELECT referenced_documents FROM chat_messages WHERE role = 'assistant'")
        assert json.loads(rows[0]["referenced_documents"]) == ["doc_z"]

    async def test_attachment_round_trip_and_forced_retrieval(self, db):
        """document_ids on the request are stored on the conversation, a deleted id
        is dropped, and an attachment retrieves even when the previous turn used tools."""
        from laya.pipeline import chat as chat_mod
        from tests.conftest import insert_test_document

        await insert_test_document(db, document_id="doc_a", title="Roadmap", summary="Plan summary")
        conv_id = await _create_conversation(db, "conv_attach")
        # Previous assistant turn used tools: ambient retrieval would be skipped.
        await db.execute(
            "INSERT INTO chat_messages (message_id, timestamp, role, content, conversation_id, tool_calls_json) "
            "VALUES ('m0', '2026-01-01 00:00:00', 'assistant', 'x', ?, '[{\"name\": \"search_cards\"}]')",
            (conv_id,),
        )
        await db.commit()
        captured = {}

        async def fake_retrieve(message, space_id=None, token_budget=2000, document_ids=None):
            captured["document_ids"] = document_ids
            captured["budget"] = token_budget
            return {"context_text": "", "result_count": 0, "signals_used": []}

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=_mock_llm_response("ok")), \
             patch("laya.pipeline.chat._retrieve_context", side_effect=fake_retrieve), \
             patch("laya.pipeline.chat.build_chat_messages", wraps=chat_mod.build_chat_messages) as build, \
             patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
            await chat_mod.process_chat_message("summarise", conversation_id=conv_id, document_ids=["doc_a", "doc_gone"])
        assert captured["document_ids"] == ["doc_a"]
        assert captured["budget"] == 3500
        kwargs = build.call_args.kwargs
        assert kwargs["has_documents"] is True
        assert "[doc:doc_a]" in kwargs["document_context"] and "Plan summary" in kwargs["document_context"]
        rows = await db.execute_fetchall("SELECT document_ids FROM chat_conversations WHERE conversation_id = ?", (conv_id,))
        assert json.loads(rows[0]["document_ids"]) == ["doc_a", "doc_gone"]

        # Second turn without document_ids keeps the stored attachment and still retrieves.
        captured.clear()
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=_mock_llm_response("ok")), \
             patch("laya.pipeline.chat._retrieve_context", side_effect=fake_retrieve), \
             patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
            await chat_mod.process_chat_message("more", conversation_id=conv_id)
        assert captured["document_ids"] == ["doc_a"]

    async def test_no_documents_skips_prompt_block(self, db):
        from laya.pipeline import chat as chat_mod

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=_mock_llm_response("ok")) as llm, \
             patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.pipeline.chat.build_chat_messages", wraps=chat_mod.build_chat_messages) as build, \
             patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
            await chat_mod.process_chat_message("hello")
        assert build.call_args.kwargs["has_documents"] is False
        system = llm.await_args.kwargs["messages"][0]["content"]
        assert "Corpus Documents" not in system

    async def test_retrieval_includes_document_signals(self, db):
        from laya.pipeline.chat import _retrieve_context

        with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]) as card_ms, \
             patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[self._doc_hit()]):
            ctx = await _retrieve_context("deadline", space_id="default", document_ids=["doc_a"])
        assert '[Doc doc_a "Roadmap" p.3 §Dates] The deadline is 2026-09-30.' in ctx["context_text"]
        assert ctx["signals_used"] == 6
        # Card search is filtered to card vectors.
        assert card_ms.await_args.kwargs["where"] == {"$and": [
            {"content_type": {"$nin": ["document_chunk", "document_summary"]}}, {"space_id": "default"},
        ]}

    async def test_format_context_item_document_branches(self):
        from laya.pipeline.chat import _format_context_item

        base = {"type": "document", "document_id": "doc_1", "text": "T"}
        assert _format_context_item({**base, "metadata": {"title": "A", "page_start": 2, "page_end": 2}}) == '[Doc doc_1 "A" p.2] T'
        assert _format_context_item({**base, "metadata": {"title": "A", "page_start": 2, "page_end": 4, "heading": "H"}}) == '[Doc doc_1 "A" pp.2-4 §H] T'
        assert _format_context_item({**base, "metadata": {"title": "A", "is_summary": True}}) == '[Doc doc_1 "A" summary] T'
        assert _format_context_item({**base, "metadata": {"title": "A"}}) == '[Doc doc_1 "A"] T'

    async def test_conversation_documents_endpoint_and_listing(self, db):
        from laya.main import app
        from tests.conftest import insert_test_document

        await insert_test_document(db, document_id="doc_a")
        conv_id = await _create_conversation(db, "conv_docs")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/chat/conversations/{conv_id}/documents", json={"document_ids": ["doc_a", "doc_missing"]})
            assert resp.status_code == 200
            assert resp.json()["document_ids"] == ["doc_a"]
            listing = (await c.get("/chat/conversations")).json()
            assert listing[0]["document_ids"] == ["doc_a"]
            assert (await c.put("/chat/conversations/conv_nope/documents", json={"document_ids": []})).status_code == 404



def test_doc_ref_pattern():
    from laya.pipeline.chat import referenced_document_ids

    text = "[doc:doc_1] [doc:doc_2 p.3] [doc:doc_3 page 4] [doc:doc_4 p.5-7] [doc:doc_1 p.9] [doc:bad id]"
    assert referenced_document_ids(text) == ["doc_1", "doc_2", "doc_3", "doc_4"]
