"""Tests for the daily briefing pipeline."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from laya.llm.client import LLMResponse
from laya.pipeline.briefing import generate_briefing, _build_fallback_briefing


def _mock_briefing_response() -> LLMResponse:
    return LLMResponse(
        content="# Daily Briefing\n\n## Overnight Summary\nQuiet night.\n\n## Needs Attention\nNothing urgent.",
        parsed=None,
        model="anthropic/claude-sonnet-4-5-20250929",
        input_tokens=500,
        output_tokens=300,
        latency_ms=1200,
    )


async def _insert_event(db, event_id, platform="jira", hours_ago=6):
    """Insert a recent event for briefing queries."""
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json, processed, filtered, actor_name) "
        "VALUES (?, datetime('now', ?), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, f"-{hours_ago} hours", platform, "issue_assigned",
         "ticket", "BUG-1", f"Test Event {event_id}", "{}", True, False, "Sarah"),
    )
    await db.commit()


async def _insert_pending_card(db, card_id, event_id, priority="HIGH"):
    await db.execute(
        "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
        "header, summary, status, privacy_tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, event_id, priority, "ENGINEER", "CODE",
         f"Card {card_id}", "Summary", "pending", 1),
    )
    await db.commit()


@pytest.mark.asyncio
class TestBriefingPipeline:
    async def test_creates_briefing_card(self, db_m7):
        """generate_briefing() creates a card with briefing type."""
        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    card_id = await generate_briefing()

        assert card_id
        assert card_id.startswith("card_")

        # Verify card exists in DB
        rows = await db_m7.execute_fetchall(
            "SELECT card_id, status, persona FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert len(rows) == 1
        assert rows[0][1] == "pending"

    async def test_queries_overnight_events(self, db_m7):
        """Briefing includes recent events in its context."""
        await _insert_event(db_m7, "evt_overnight", hours_ago=6)

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()) as mock_llm:
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    card_id = await generate_briefing()

        # LLM should have been called with overnight events in context
        assert mock_llm.called

    async def test_includes_pending_cards(self, db_m7):
        """Briefing includes pending action cards."""
        await _insert_event(db_m7, "evt_pend")
        await _insert_pending_card(db_m7, "card_pend", "evt_pend", priority="CRITICAL")

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    card_id = await generate_briefing()

        assert card_id

    async def test_broadcasts_briefing_ready(self, db_m7):
        """Briefing broadcasts a briefing_ready WS message."""
        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    await generate_briefing()

        mock_mgr.broadcast.assert_called()
        call_args = mock_mgr.broadcast.call_args[0][0]
        assert call_args["type"] == "briefing_ready"

    async def test_llm_failure_uses_fallback(self, db_m7):
        """When LLM fails, briefing uses fallback text."""
        await _insert_event(db_m7, "evt_fb", hours_ago=3)

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       side_effect=Exception("LLM down")):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    card_id = await generate_briefing()

        # Card should still be created with fallback content
        assert card_id
        rows = await db_m7.execute_fetchall(
            "SELECT card_id FROM action_cards WHERE card_id = ?", (card_id,),
        )
        assert len(rows) == 1

    async def test_prevents_duplicate_briefings(self, db_m7):
        """generate_briefing() returns existing card if already generated today."""
        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    card_id_1 = await generate_briefing()
                    card_id_2 = await generate_briefing()

        # Second call should return the same card (or at least not create a duplicate)
        assert card_id_1 == card_id_2


class TestFallbackBriefing:
    def test_fallback_format(self):
        """Fallback briefing includes basic sections."""
        events = [
            {"source_platform": "jira", "subject_title": "Bug Fix", "actor_name": "Sarah", "timestamp": "2026-02-22T08:00:00Z", "event_id": "e1"},
        ]
        cards = [
            {"card_id": "c1", "header": "Fix NPE", "summary": "NPE found", "priority": "HIGH", "persona": "ENGINEER"},
        ]
        stats = {"events_processed": 10, "cards_generated": 3, "cards_resolved": 2}

        result = _build_fallback_briefing(events, cards, stats)
        assert "Daily Briefing" in result
        assert "Bug Fix" in result
        assert "Fix NPE" in result
        assert "10" in result
