"""Integration tests for the chat pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline.chat import process_chat_message
from tests.conftest import insert_test_event, insert_test_card


def _mock_llm_response(content: str):
    """Create a mock LLM response matching laya.llm.client.LLMResponse shape."""
    resp = MagicMock()
    resp.content = content
    resp.parsed = None
    resp.model = "test-model"
    resp.input_tokens = 200
    resp.output_tokens = 100
    resp.latency_ms = 50
    resp.finish_reason = "stop"
    resp.tool_calls = None
    resp.raw_message_dict = None
    return resp


@pytest.mark.asyncio
class TestChatIntegration:
    """Integration tests for the chat pipeline."""

    async def test_chat_returns_response_with_refs(self, db):
        """Chat with seeded context returns card references."""
        # Seed an event and card
        await insert_test_event(db, event_id="evt_chat_int", platform="jira",
                                subject_id="BUG-1", subject_title="NPE in PaymentService")
        await insert_test_card(db, card_id="card_chat_int", event_id="evt_chat_int",
                               header="Fix NPE in PaymentService",
                               summary="NullPointerException found", status="ready")

        mock_resp = _mock_llm_response(
            "The NPE bug is tracked in [card:card_chat_int]. It's a high priority issue."
        )

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result = await process_chat_message("What about the NPE bug?")

        assert "card_chat_int" in result.referenced_cards
        assert result.message.role == "assistant"

    async def test_chat_history_accumulates(self, db):
        """Multiple chat messages accumulate in history within same conversation."""
        mock_resp1 = _mock_llm_response("First response.")
        mock_resp2 = _mock_llm_response("Second response.")

        # First message creates a conversation
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp1):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result1 = await process_chat_message("Hello")

        conv_id = result1.message.conversation_id

        # Second message in the same conversation
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp2):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                await process_chat_message("How are you?", conversation_id=conv_id)

        # Check history has 4 messages (2 user + 2 assistant) in this conversation
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM chat_messages WHERE conversation_id = ?",
            (conv_id,),
        )
        assert rows[0][0] == 4

    async def test_chat_empty_db_returns_response(self, db):
        """Chat works even with an empty DB (no cards/events)."""
        mock_resp = _mock_llm_response(
            "I don't have any cards or events to discuss yet."
        )

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result = await process_chat_message("What's going on?")

        assert result.message.content is not None
        assert len(result.message.content) > 0

    async def test_chat_llm_failure_returns_graceful_error(self, db):
        """LLM failure returns a graceful error message."""
        async def _fail(**kwargs):
            raise Exception("LLM connection failed")

        with patch("laya.pipeline.chat.llm_call", side_effect=_fail):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result = await process_chat_message("Hello")

        assert "error" in result.message.content.lower() or "sorry" in result.message.content.lower()

    async def test_chat_creates_conversation(self, db):
        """First chat message auto-creates a conversation with the placeholder title."""
        mock_resp = _mock_llm_response("Hello there!")

        # Suppress title generation so we can deterministically observe the
        # placeholder title that the pipeline seeds on creation.
        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
                    result = await process_chat_message("Hi")

        assert result.message.conversation_id is not None

        # Verify conversation row exists with the placeholder title
        rows = await db.execute_fetchall(
            "SELECT conversation_id, title FROM chat_conversations WHERE conversation_id = ?",
            (result.message.conversation_id,),
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "New Chat"

    async def test_chat_generates_title_via_router(self, db):
        """First message kicks off async title generation through the router model."""
        import asyncio
        from laya import tasks as tasks_mod

        mock_resp = _mock_llm_response("Debugging Auth Flow")

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result = await process_chat_message("Why is auth failing?")

        # Wait for the background title-generation task to finish before asserting
        pending = [t for t in list(tasks_mod._tracked) if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        rows = await db.execute_fetchall(
            "SELECT title FROM chat_conversations WHERE conversation_id = ?",
            (result.message.conversation_id,),
        )
        assert rows[0]["title"] == "Debugging Auth Flow"

    async def test_title_generation_skipped_after_first_message(self, db):
        """Subsequent messages in the same conversation do not retrigger title generation."""
        import asyncio
        from laya import tasks as tasks_mod

        first_resp = _mock_llm_response("First answer")
        second_resp = _mock_llm_response("Should Not Overwrite")

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=first_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.chat._should_generate_title", new_callable=AsyncMock, return_value=False):
                    first = await process_chat_message("Initial message")

        # Manually set a user-chosen title to simulate a rename
        await db.execute(
            "UPDATE chat_conversations SET title = ? WHERE conversation_id = ?",
            ("User Chose This", first.message.conversation_id),
        )
        await db.commit()

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=second_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                await process_chat_message(
                    "Follow-up", conversation_id=first.message.conversation_id
                )

        pending = [t for t in list(tasks_mod._tracked) if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        rows = await db.execute_fetchall(
            "SELECT title FROM chat_conversations WHERE conversation_id = ?",
            (first.message.conversation_id,),
        )
        assert rows[0]["title"] == "User Chose This"

    async def test_chat_with_space_id(self, db):
        """Chat message with space_id is stored correctly."""
        mock_resp = _mock_llm_response("Space-scoped response.")

        with patch("laya.pipeline.chat.llm_call", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                result = await process_chat_message(
                    "Hello", space_id="default"
                )

        # Verify conversation was created with space_id
        rows = await db.execute_fetchall(
            "SELECT space_id FROM chat_conversations WHERE conversation_id = ?",
            (result.message.conversation_id,),
        )
        assert rows[0]["space_id"] == "default"
