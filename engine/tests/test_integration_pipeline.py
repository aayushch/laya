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
from laya.pipeline.stager import ActionCardData, run_stager


def _make_event(event_id="evt_int_001", platform="jira", event_type="issue_assigned",
                actor_email="sarah@company.com", subject_type="ticket",
                subject_id="BUG-1234", title="NPE in PaymentService", body="NullPointerException") -> LayaEvent:
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
            "payload": '{"body": "Investigation complete."}',
        }
    ],
    "privacy_tier": 2,
}


def _mock_llm_response(parsed_dict):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(parsed_dict)
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 300
    resp.usage.completion_tokens = 150
    return resp


async def _store_event(db, event):
    """Insert an event into the DB so router/stager can read it."""
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "actor_name, actor_email, subject_type, subject_id, subject_title, content_body, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event.event_id, event.timestamp.isoformat(), event.source.platform,
         event.source.raw_event_type, event.actor.name, event.actor.email,
         event.subject.type, event.subject.id, event.subject.title,
         event.content.body, json.dumps({"raw": "data"})),
    )
    await db.commit()


@pytest.mark.asyncio
class TestPipelineIntegration:
    """Full pipeline integration tests."""

    async def test_full_pipeline_creates_card(self, db_m8, sample_team, sample_rules):
        """Full pipeline: event → ingest → route → stage → emit creates a card."""
        event = _make_event()
        await _store_event(db_m8, event)

        router_resp = _mock_llm_response(MOCK_ROUTER_RESULT)
        stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)

        call_count = 0

        async def _mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return router_resp if call_count == 1 else stager_resp

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship = await run_ingest(event)

        assert relationship in ("teammate", "manager", "external", "bot")

        with patch("litellm.acompletion", side_effect=_mock_completion):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001", "stager": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.router.embed_document", new_callable=AsyncMock):
                    with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                        with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                            router_output = await run_router(event, relationship)

        assert router_output.persona == "ENGINEER"
        assert router_output.category == "CODE"

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=stager_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.stager.memory_search", new_callable=AsyncMock, return_value=[]):
                    stager_output = await run_stager(event, router_output)

        assert stager_output.header == "Fix NPE in PaymentService"

        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.manager", MagicMock(broadcast=AsyncMock())):
                with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock):
                    card_id = await run_emit(event, router_output, stager_output)

        assert card_id.startswith("card_")

        # Verify card exists in DB
        rows = await db_m8.execute_fetchall(
            "SELECT card_id, header, persona, status FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert len(rows) == 1
        assert rows[0]["header"] == "Fix NPE in PaymentService"
        assert rows[0]["persona"] == "ENGINEER"
        assert rows[0]["status"] == "pending"

    async def test_bot_event_resolved_as_bot(self, db_m8, sample_team, sample_rules):
        """Bot team member is resolved with their team role."""
        event = _make_event(
            event_id="evt_bot",
            actor_email="ci@company.com",  # matches CI Bot in sample_team
        )
        await _store_event(db_m8, event)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship = await run_ingest(event)

        assert relationship == "bot"

    async def test_router_failure_no_crash(self, db_m8, sample_team):
        """Router LLM failure doesn't crash — raises exception."""
        event = _make_event(event_id="evt_fail")
        await _store_event(db_m8, event)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship = await run_ingest(event)

        async def _llm_fail(**kwargs):
            raise Exception("LLM down")

        with patch("litellm.acompletion", side_effect=_llm_fail):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with patch("laya.pipeline.router.embed_document", new_callable=AsyncMock):
                    with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                        with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                            with pytest.raises(Exception, match="LLM down"):
                                await run_router(event, relationship)

    async def test_simple_event_no_research(self, db_m8, sample_team):
        """Non-research event creates a card without workspace."""
        event = _make_event(event_id="evt_simple")
        await _store_event(db_m8, event)

        # Router says no research needed
        no_research = {**MOCK_ROUTER_RESULT, "requires_research": False}
        router_resp = _mock_llm_response(no_research)
        stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship = await run_ingest(event)

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=router_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with patch("laya.pipeline.router.embed_document", new_callable=AsyncMock):
                    with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                        with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                            router_output = await run_router(event, relationship)

        assert not router_output.requires_research

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=stager_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
                with patch("laya.pipeline.stager.memory_search", new_callable=AsyncMock, return_value=[]):
                    stager_output = await run_stager(event, router_output)

        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.manager", MagicMock(broadcast=AsyncMock())):
                with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock):
                    card_id = await run_emit(event, router_output, stager_output)

        rows = await db_m8.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert rows[0]["has_workspace"] == 0

    async def test_research_event_sets_workspace(self, db_m8, sample_team):
        """Event requiring research creates card with has_workspace=True."""
        event = _make_event(event_id="evt_research")
        await _store_event(db_m8, event)

        research = {**MOCK_ROUTER_RESULT, "requires_research": True}
        router_resp = _mock_llm_response(research)
        stager_resp = _mock_llm_response(MOCK_STAGER_RESULT)

        with patch("laya.pipeline.ingest.load_team", return_value=sample_team):
            with patch("laya.config.load_team", return_value=sample_team):
                relationship = await run_ingest(event)

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=router_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with patch("laya.pipeline.router.embed_document", new_callable=AsyncMock):
                    with patch("laya.pipeline.router.memory_search", new_callable=AsyncMock, return_value=[]):
                        with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
                            router_output = await run_router(event, relationship)

        assert router_output.requires_research

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=stager_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"stager": "claude-sonnet-4-5-20250929"}}):
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
                    card_id = await run_emit(event, router_output, stager_output, worker_results)

        rows = await db_m8.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert rows[0]["has_workspace"] == 1
