# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Stager pipeline step."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.card import ActionCardData, StagedOutput
from laya.pipeline.stager import run_stager


@pytest.mark.asyncio
class TestStager:
    async def test_run_stager_returns_action_card_data(
        self, db, sample_event, sample_router_output_engineer,
        mock_llm_stager, sample_worker_result,
    ):
        """run_stager returns ActionCardData from LLM response."""
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            result = await run_stager(
                sample_event, sample_router_output_engineer, [sample_worker_result]
            )

        assert isinstance(result, ActionCardData)
        assert "PaymentService" in result.header
        assert len(result.intelligence_report) == 5
        assert result.staged_output.type == "code_fix"
        assert result.privacy_tier == 2

    async def test_run_stager_without_workers(
        self, db, sample_event, sample_router_output_comms, mock_llm_stager,
    ):
        """run_stager works with worker_results=None (simple path)."""
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            result = await run_stager(
                sample_event, sample_router_output_comms, worker_results=None
            )

        assert isinstance(result, ActionCardData)
        assert result.header != ""
        assert result.summary != ""

    async def test_run_stager_propagates_llm_error(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """A transport/LLM failure propagates so the durable queue retries the
        event instead of swallowing it into a degraded fallback card with the
        event marked completed (review §2 pipeline — P3-5)."""
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            with patch("laya.pipeline.stager.llm_call", new_callable=AsyncMock, side_effect=Exception("LLM timeout")):
                with pytest.raises(Exception, match="LLM timeout"):
                    await run_stager(sample_event, sample_router_output_engineer)

    async def test_run_stager_falls_back_on_parse_failure(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """A PARSE failure (LLM returned unparseable content, not a transport
        error) still yields the minimal fallback card (review §2 pipeline)."""
        bad = MagicMock()
        bad.parsed = None
        bad.content = "this is not json"
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            with patch("laya.pipeline.stager.llm_call", new_callable=AsyncMock, return_value=bad):
                result = await run_stager(sample_event, sample_router_output_engineer)

        assert isinstance(result, ActionCardData)
        assert "Review:" in result.header
        assert result.staged_output.type == "summary"

    async def test_stager_parses_suggested_actions(
        self, db, sample_event, sample_router_output_engineer,
        mock_llm_stager, sample_worker_result,
    ):
        """run_stager correctly parses suggested_actions with JSON string payload."""
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]):
            result = await run_stager(
                sample_event, sample_router_output_engineer, [sample_worker_result]
            )

        assert len(result.suggested_actions) == 2
        assert result.suggested_actions[0].action_id == "act_comment_jira"
        assert result.suggested_actions[0].target_platform == "jira"
        # Payload should be parsed from JSON string to dict
        assert isinstance(result.suggested_actions[0].payload, dict)
        assert "body" in result.suggested_actions[0].payload

    async def test_stager_uses_related_context(
        self, db, sample_event, sample_router_output_engineer, mock_llm_stager,
    ):
        """run_stager passes related context from ChromaDB to the LLM."""
        mock_context = [
            {"id": "ctx_1", "document": "Past NPE fix in OrderService", "metadata": {"source_platform": "jira"}, "distance": 0.2}
        ]
        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=mock_context):
            result = await run_stager(sample_event, sample_router_output_engineer)

        assert isinstance(result, ActionCardData)
