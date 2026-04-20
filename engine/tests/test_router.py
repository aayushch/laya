"""Tests for the ROUTER pipeline step."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.classification import RouterOutput
from laya.pipeline.router import _store_entities, run_router
from tests.conftest import MOCK_COMMS_RESPONSE, MOCK_ROUTER_RESPONSE, insert_test_event


@pytest.mark.asyncio
async def test_router_classifies_jira_bug(db, sample_event, mock_llm_router, mock_chromadb):
    """Router classifies a Jira bug as CODE/ENGINEER/HIGH."""
    await insert_test_event(db, event_id=sample_event.event_id)

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        result = await run_router(sample_event, "teammate")

    assert isinstance(result, RouterOutput)
    assert result.category.value == "CODE"
    assert result.persona.value == "ENGINEER"
    assert result.priority.value == "HIGH"
    assert result.confidence == 0.92
    assert result.requires_research is True
    assert len(result.entities) == 2
    assert len(result.research_plan) == 3

    # Verify stored in SQLite
    async with db.execute(
        "SELECT router_output, processed FROM events WHERE event_id = ?",
        (sample_event.event_id,),
    ) as cursor:
        row = await cursor.fetchone()
        assert row["processed"] == 1
        stored = json.loads(row["router_output"])
        assert stored["category"] == "CODE"
        assert stored["persona"] == "ENGINEER"


@pytest.mark.asyncio
async def test_router_classifies_slack_message(db, slack_event, mock_llm_comms, mock_chromadb):
    """Router classifies a Slack message as COMMS/COMMS/MEDIUM."""
    await insert_test_event(
        db, event_id=slack_event.event_id, platform="slack",
        raw_event_type="message_received", subject_type="thread",
        subject_id="thread-random", subject_title="Hey everyone",
        actor_name="Mike", actor_email="mike@company.com",
        content_body="Hey everyone!",
    )

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        result = await run_router(slack_event, "manager")

    assert result.category.value == "COMMS"
    assert result.persona.value == "COMMS"
    assert result.priority.value == "MEDIUM"
    assert result.requires_research is False


@pytest.mark.asyncio
async def test_router_can_classify_as_sales(db, slack_event, mock_chromadb):
    """Router accepts SALES as a valid persona in its classification output."""
    await insert_test_event(
        db, event_id=slack_event.event_id, platform="gmail",
        raw_event_type="message_received", subject_type="thread",
        subject_id="thread-sales-01", subject_title="Re: Acme renewal quote",
        actor_name="Dana", actor_email="dana@acme.com",
        content_body="Following up on the renewal quote we discussed last week.",
    )

    sales_response = {
        "category": "COMMS",
        "persona": "SALES",
        "priority": "HIGH",
        "confidence": 0.88,
        "entities": [{"entity_type": "person", "value": "Dana", "platform": "gmail"}],
        "research_plan": ["Pull past account history for Acme", "Confirm quote status in CRM"],
        "requires_research": False,
        "secondary_persona": None,
        "reasoning": "External customer follow-up about a pending renewal quote.",
    }
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(sales_response)
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 400
    mock_response.usage.completion_tokens = 200

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await run_router(slack_event, "external")

    assert result.persona.value == "SALES"
    assert result.category.value == "COMMS"


@pytest.mark.asyncio
async def test_router_handles_llm_failure(db, sample_event, mock_chromadb):
    """Router returns safe defaults when LLM call fails."""
    await insert_test_event(db, event_id=sample_event.event_id)

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("API down")):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with pytest.raises(Exception, match="API down"):
                    await run_router(sample_event, "teammate")


@pytest.mark.asyncio
async def test_router_handles_malformed_json(db, sample_event, mock_chromadb):
    """Router returns safe default when LLM returns invalid JSON."""
    await insert_test_event(db, event_id=sample_event.event_id)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not json at all {{"
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await run_router(sample_event, "teammate")

    # Should get safe default
    assert result.confidence == 0.0
    assert result.category.value == "OPS"
    assert result.priority.value == "MEDIUM"


@pytest.mark.asyncio
async def test_router_queries_related_context(db, sample_event, mock_llm_router, mock_chromadb):
    """Router calls memory_search for related past events."""
    await insert_test_event(db, event_id=sample_event.event_id)

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        await run_router(sample_event, "teammate")

    mock_chromadb["memory_search"].assert_called_once()
    call_args = mock_chromadb["memory_search"].call_args
    assert "NPE in PaymentService" in call_args.args[0]


@pytest.mark.asyncio
async def test_entities_stored_in_db(db, sample_event, mock_llm_router, mock_chromadb):
    """Router stores extracted entities in the entities table."""
    await insert_test_event(db, event_id=sample_event.event_id)

    with patch("laya.pipeline.feedback.query_feedback_patterns", new_callable=AsyncMock, return_value=[]):
        await run_router(sample_event, "teammate")

    async with db.execute("SELECT * FROM entities ORDER BY canonical_name") as cursor:
        rows = await cursor.fetchall()

    assert len(rows) == 2
    names = [row["canonical_name"] for row in rows]
    assert "BUG-1234" in names
    assert "PaymentService.java" in names

    # Check the ticket entity
    ticket_row = next(r for r in rows if r["canonical_name"] == "BUG-1234")
    assert ticket_row["entity_type"] == "ticket"
    assert ticket_row["link_method"] == "explicit"
    refs = json.loads(ticket_row["platform_refs"])
    assert "jira" in refs


@pytest.mark.asyncio
async def test_entity_merge_on_duplicate(db, sample_event, mock_chromadb):
    """Entities with same canonical_name get platform_refs merged."""
    await insert_test_event(db, event_id=sample_event.event_id)

    # Pre-insert an entity with the same canonical_name but different platform
    await db.execute(
        """INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs, link_method, confidence)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("ent_existing", "ticket", "BUG-1234", json.dumps({"bitbucket": ["PR-related-to-BUG-1234"]}), "explicit", 1.0),
    )
    await db.commit()

    # Create a RouterOutput with BUG-1234 entity
    router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
    await _store_entities(sample_event.event_id, router_output)

    # Verify merge
    async with db.execute(
        "SELECT platform_refs FROM entities WHERE canonical_name = 'BUG-1234'"
    ) as cursor:
        row = await cursor.fetchone()

    refs = json.loads(row[0])
    assert "bitbucket" in refs  # Original
    assert "jira" in refs  # Merged from router output
