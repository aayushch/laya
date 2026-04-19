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
        # First attempt uses 2048; retry doubles to 4096.
        assert mock.await_args_list[0].kwargs["max_tokens"] == 2048
        assert mock.await_args_list[1].kwargs["max_tokens"] == 4096

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
