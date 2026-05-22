# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the full event pipeline: ingest → route → stage → emit."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.emit import run_emit
from laya.pipeline.ingest import run_ingest
from laya.pipeline.router import run_router
from laya.pipeline.stager import run_stager
from laya.models.card import ActionCardData

from tests.conftest import insert_test_event


def _make_event(event_id="evt_int_001", platform="jira", event_type="issue_assigned",
                actor_email="sarah@company.com", subject_type="ticket",
                subject_id="BUG-1234", title="NPE in PaymentService",
                body="NullPointerException") -> LayaEvent:
    """Create a test event."""
    return LayaEvent(
        event_id=event_id,
        timestamp=datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc),
        source={"platform": platform, "raw_event_type": event_type},
        actor={"name": "Sarah Chen", "email": actor_email},
        subject={"type": subject_type, "id": subject_id, "title": title},
        content={"body": body, "attachments": [], "metadata": {}},
    )


MOCK_ROUTER_RESULT = {
    "category": "CODE",
    "persona": "ENGINEER",
    "priority": "HIGH",
    "confidence": 0.92,
    "entities": [
        {"entity_type": "ticket", "value": "BUG-1234", "platform": "jira"},
    ],
    "research_plan": ["Check recent changes"],
    "requires_research": False,
    "secondary_persona": None,
    "reasoning": "Bug report needs investigation.",
}

MOCK_STAGER_RESULT = {
    "header": "Fix NPE in PaymentService",
    "summary": "NullPointerException in PaymentService.java",
    "intelligence_report": ["NPE at line 42"],
    "staged_output": {"type": "code_fix", "content": "Add null check"},
    "suggested_actions": [
        {
            "action_id": "act_comment",
            "label": "Post Comment",
            "action_type": "comment",
            "target_platform": "jira",
            "payload": {"body": "Investigation complete."},
        }
    ],
    "privacy_tier": 2,
}


def _mock_llm_response(parsed_dict):
    """Create a mock LLM response (laya.llm.client.LLMResponse-like)."""
    resp = MagicMock()
    resp.content = json.dumps(parsed_dict)
    resp.parsed = parsed_dict
    resp.model = "test-model"
    resp.input_tokens = 300
    resp.output_tokens = 150
    resp.latency_ms = 100
    resp.finish_reason = "stop"
    resp.tool_calls = None
    resp.raw_message_dict = None
    return resp


async def _store_event(db, event):
    """Insert an event into the DB so router/stager can read it."""
    await insert_test_event(
        db,
        event_id=event.event_id,
        platform=event.source.platform,
        raw_event_type=event.source.raw_event_type,
        subject_type=event.subject.type,
        subject_id=event.subject.id,
        subject_title=event.subject.title,
        actor_name=event.actor.name,
        actor_email=event.actor.email,
        content_body=event.content.body,
    )


@pytest.mark.asyncio
class TestPipelineIntegration:
    """Full pipeline integration tests."""

    async def test_full_pipeline_creates_card(self, db, sample_team, sample_rules):
        """Full pipeline: event -> ingest -> route -> stage -> emit creates a card."""
        event = _make_event()
        await _store_event(db, event)

        router_output = RouterOutput(**MOCK_ROUTER_RESULT)
        stager_output = ActionCardData(**MOCK_STAGER_RESULT)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship, _participant_roles = await run_ingest(event)

        assert relationship in ("teammate", "manager", "external", "bot")

        # Mock all router dependencies
        mock_router_resp = _mock_llm_response(MOCK_ROUTER_RESULT)
        with patch("laya.pipeline.router.llm_call", new_callable=AsyncMock, return_value=mock_router_resp):
            with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                    router_output = await run_router(event, relationship)

        assert router_output.persona == "ENGINEER"
        assert router_output.category == "CODE"

        # Mock stager dependencies
        mock_stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)
        with patch("laya.pipeline.stager.llm_call", new_callable=AsyncMock, return_value=mock_stager_resp):
            with patch("laya.pipeline.stager.memory_search", new_callable=AsyncMock, return_value=[]):
                stager_output = await run_stager(event, router_output)

        assert stager_output.header == "Fix NPE in PaymentService"

        # Mock emit dependencies
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.manager", MagicMock(broadcast=AsyncMock())):
                with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update"):
                        card_id = await run_emit(event, router_output, stager_output)

        assert card_id.startswith("card_")

        # Verify card exists in DB with status 'ready'
        rows = await db.execute_fetchall(
            "SELECT card_id, header, persona, status FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert len(rows) == 1
        assert rows[0]["header"] == "Fix NPE in PaymentService"
        assert rows[0]["persona"] == "ENGINEER"
        assert rows[0]["status"] == "ready"

    async def test_bot_event_resolved_as_bot(self, db, sample_team, sample_rules):
        """Bot team member is resolved with their team role."""
        event = _make_event(
            event_id="evt_bot",
            actor_email="ci@company.com",  # matches CI Bot in sample_team
        )
        await _store_event(db, event)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship, _pr = await run_ingest(event)

        assert relationship == "bot"

    async def test_router_failure_no_crash(self, db, sample_team):
        """Router LLM failure doesn't crash -- raises exception."""
        event = _make_event(event_id="evt_fail")
        await _store_event(db, event)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship, _pr = await run_ingest(event)

        async def _llm_fail(**kwargs):
            raise Exception("LLM down")

        with patch("laya.pipeline.router.llm_call", side_effect=_llm_fail):
            with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                        with pytest.raises(Exception, match="LLM down"):
                            await run_router(event, relationship)

    async def test_simple_event_no_research(self, db, sample_team):
        """Non-research event creates a card without workspace."""
        event = _make_event(event_id="evt_simple")
        await _store_event(db, event)

        no_research = {**MOCK_ROUTER_RESULT, "requires_research": False}
        mock_router_resp = _mock_llm_response(no_research)
        mock_stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship, _pr = await run_ingest(event)

        with patch("laya.pipeline.router.llm_call", new_callable=AsyncMock, return_value=mock_router_resp):
            with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                    router_output = await run_router(event, relationship)

        assert not router_output.requires_research

        with patch("laya.pipeline.stager.llm_call", new_callable=AsyncMock, return_value=mock_stager_resp):
            with patch("laya.pipeline.stager.memory_search", new_callable=AsyncMock, return_value=[]):
                stager_output = await run_stager(event, router_output)

        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.manager", MagicMock(broadcast=AsyncMock())):
                with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update"):
                        card_id = await run_emit(event, router_output, stager_output)

        rows = await db.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert rows[0]["has_workspace"] == 0

    async def test_research_event_sets_workspace(self, db, sample_team):
        """Event requiring research creates card with has_workspace=True."""
        event = _make_event(event_id="evt_research")
        await _store_event(db, event)

        research = {**MOCK_ROUTER_RESULT, "requires_research": True}
        mock_router_resp = _mock_llm_response(research)
        mock_stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship, _pr = await run_ingest(event)

        with patch("laya.pipeline.router.llm_call", new_callable=AsyncMock, return_value=mock_router_resp):
            with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                    router_output = await run_router(event, relationship)

        assert router_output.requires_research

        with patch("laya.pipeline.stager.llm_call", new_callable=AsyncMock, return_value=mock_stager_resp):
            with patch("laya.pipeline.stager.memory_search", new_callable=AsyncMock, return_value=[]):
                stager_output = await run_stager(event, router_output)

        # Pass worker_results with a session_id to simulate research
        from laya.workers.base import WorkerResult
        worker_results = [WorkerResult(
            persona="ENGINEER",
            findings={"root_cause": "null ID"},
            drafted_output={"fix": "add null check"},
            session_id="sess_test_123",
        )]

        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.manager", MagicMock(broadcast=AsyncMock())):
                with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update"):
                        card_id = await run_emit(event, router_output, stager_output, worker_results)

        rows = await db.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert rows[0]["has_workspace"] == 1
