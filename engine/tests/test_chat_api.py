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
