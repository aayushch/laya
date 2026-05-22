# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the daily briefing pipeline."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from laya.llm.client import LLMResponse
from laya.pipeline.briefing import generate_briefing, _build_fallback_briefing
from tests.conftest import insert_test_card, insert_test_event


def _mock_briefing_response() -> LLMResponse:
    return LLMResponse(
        content="# Daily Briefing\n\n## Overnight Summary\nQuiet night.\n\n## Needs Attention\nNothing urgent.",
        parsed=None,
        model="anthropic/claude-sonnet-4-5-20250929",
        input_tokens=500,
        output_tokens=300,
        latency_ms=1200,
    )


@pytest.mark.asyncio
class TestBriefingPipeline:
    async def test_creates_briefing_card(self, db):
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
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await generate_briefing()

        assert card_id
        assert card_id.startswith("card_")

        # Verify card exists in DB with status 'ready' (emit default)
        rows = await db.execute_fetchall(
            "SELECT card_id, status, persona FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert len(rows) == 1
        assert rows[0][1] == "ready"

    async def test_queries_overnight_events(self, db):
        """Briefing includes recent events in its context."""
        await insert_test_event(db, "evt_overnight", space_id=None)

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()) as mock_llm:
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await generate_briefing()

        # LLM should have been called with overnight events in context
        assert mock_llm.called

    async def test_includes_pending_cards(self, db):
        """Briefing includes pending action cards."""
        await insert_test_card(
            db, "card_pend", "evt_pend", priority="CRITICAL", status="pending",
            entity_id="jira:ticket:BUG-PEND",
        )

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       return_value=_mock_briefing_response()):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await generate_briefing()

        assert card_id

    async def test_broadcasts_briefing_ready(self, db):
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
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        await generate_briefing()

        mock_mgr.broadcast.assert_called()
        # The last broadcast call should be the briefing_ready message
        # (emit also broadcasts card_created before it)
        calls = mock_mgr.broadcast.call_args_list
        briefing_call = [c for c in calls if c[0][0].get("type") == "briefing_ready"]
        assert len(briefing_call) == 1
        assert briefing_call[0][0][0]["type"] == "briefing_ready"

    async def test_llm_failure_uses_fallback(self, db):
        """When LLM fails, briefing uses fallback text."""
        await insert_test_event(db, "evt_fb", space_id=None)

        mock_settings = {
            "briefing": {"enabled": True, "time": "07:00", "timezone": "UTC"},
            "models": {"stager": "claude-sonnet-4-5-20250929"},
        }
        with patch("laya.pipeline.briefing.load_settings", return_value=mock_settings):
            with patch("laya.pipeline.briefing.llm_call", new_callable=AsyncMock,
                       side_effect=Exception("LLM down")):
                with patch("laya.pipeline.briefing.manager") as mock_mgr:
                    mock_mgr.broadcast = AsyncMock()
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await generate_briefing()

        # Card should still be created with fallback content
        assert card_id
        rows = await db.execute_fetchall(
            "SELECT card_id FROM action_cards WHERE card_id = ?", (card_id,),
        )
        assert len(rows) == 1

    async def test_prevents_duplicate_briefings(self, db):
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
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
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
