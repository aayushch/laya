"""Integration tests for the chat pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline.chat import process_chat_message


def _mock_llm_response(content: str):
    """Create a mock LLM response with given content."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 200
    resp.usage.completion_tokens = 100
    return resp


@pytest.mark.asyncio
class TestChatIntegration:
    """Integration tests for the chat pipeline."""

    async def test_chat_returns_response_with_refs(self, db_m8):
        """Chat with seeded context returns card references."""
        # Seed an event and card
        await db_m8.execute(
            "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
            "subject_type, subject_id, subject_title, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt_chat_int", "2026-02-22T14:30:00Z", "jira", "issue_assigned",
             "ticket", "BUG-1", "NPE in PaymentService", "{}"),
        )
        await db_m8.execute(
            "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
            "header, summary, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("card_chat_int", "evt_chat_int", "HIGH", "ENGINEER", "CODE",
             "Fix NPE in PaymentService", "NullPointerException found", "pending"),
        )
        await db_m8.commit()

        mock_resp = _mock_llm_response(
            "The NPE bug is tracked in [card:card_chat_int]. It's a high priority issue."
        )

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                    result = await process_chat_message("What about the NPE bug?")

        assert "card_chat_int" in result.referenced_cards
        assert result.message.role == "assistant"

    async def test_chat_history_accumulates(self, db_m8):
        """Multiple chat messages accumulate in history."""
        mock_resp1 = _mock_llm_response("First response.")
        mock_resp2 = _mock_llm_response("Second response.")

        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[mock_resp1, mock_resp2]):
            with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                    await process_chat_message("Hello")
                    await process_chat_message("How are you?")

        # Check history has 4 messages (2 user + 2 assistant)
        rows = await db_m8.execute_fetchall("SELECT COUNT(*) FROM chat_messages")
        assert rows[0][0] == 4

    async def test_chat_empty_db_returns_response(self, db_m8):
        """Chat works even with an empty DB (no cards/events)."""
        mock_resp = _mock_llm_response("I don't have any cards or events to discuss yet.")

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                    result = await process_chat_message("What's going on?")

        assert result.message.content is not None
        assert len(result.message.content) > 0

    async def test_chat_llm_failure_returns_graceful_error(self, db_m8):
        """LLM failure returns a graceful error message."""
        async def _fail(**kwargs):
            raise Exception("LLM connection failed")

        with patch("litellm.acompletion", side_effect=_fail):
            with patch("laya.llm.client.load_settings", return_value={"models": {"chat": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.chat.memory_search", new_callable=AsyncMock, return_value=[]):
                    result = await process_chat_message("Hello")

        assert "error" in result.message.content.lower() or "sorry" in result.message.content.lower()
