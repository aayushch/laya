"""Tests for the ROUTER pipeline step."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.classification import RouterOutput
from laya.pipeline.router import _store_entities, run_router
from tests.conftest import MOCK_COMMS_RESPONSE, MOCK_ROUTER_RESPONSE


async def _insert_event(db, event):
    """Helper to insert an event row for router tests."""
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.timestamp.isoformat(),
            event.source.platform,
            event.source.raw_event_type,
            event.subject.type,
            event.subject.id,
            event.subject.title,
            "{}",
        ),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_router_classifies_jira_bug(db_full, sample_event, mock_llm_router, mock_chromadb):
    """Router classifies a Jira bug as CODE/ENGINEER/HIGH."""
    await _insert_event(db_full, sample_event)

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
    async with db_full.execute(
        "SELECT router_output, processed FROM events WHERE event_id = ?",
        (sample_event.event_id,),
    ) as cursor:
        row = await cursor.fetchone()
        assert row["processed"] == 1
        stored = json.loads(row["router_output"])
        assert stored["category"] == "CODE"
        assert stored["persona"] == "ENGINEER"


@pytest.mark.asyncio
async def test_router_classifies_slack_message(db_full, slack_event, mock_llm_comms, mock_chromadb):
    """Router classifies a Slack message as COMMS/COMMS/MEDIUM."""
    await _insert_event(db_full, slack_event)

    result = await run_router(slack_event, "manager")

    assert result.category.value == "COMMS"
    assert result.persona.value == "COMMS"
    assert result.priority.value == "MEDIUM"
    assert result.requires_research is False


@pytest.mark.asyncio
async def test_router_handles_llm_failure(db_full, sample_event, mock_chromadb):
    """Router returns safe defaults when LLM call fails."""
    await _insert_event(db_full, sample_event)

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("API down")):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            with pytest.raises(Exception, match="API down"):
                await run_router(sample_event, "teammate")


@pytest.mark.asyncio
async def test_router_handles_malformed_json(db_full, sample_event, mock_chromadb):
    """Router returns safe default when LLM returns invalid JSON."""
    await _insert_event(db_full, sample_event)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not json at all {{"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with patch("laya.llm.client.load_settings", return_value={"models": {"router": "claude-haiku-4-5-20251001"}}):
            result = await run_router(sample_event, "teammate")

    # Should get safe default
    assert result.confidence == 0.0
    assert result.category.value == "OPS"
    assert result.priority.value == "MEDIUM"


@pytest.mark.asyncio
async def test_router_embeds_event_in_chromadb(db_full, sample_event, mock_llm_router, mock_chromadb):
    """Router calls embed_document after classification."""
    await _insert_event(db_full, sample_event)

    await run_router(sample_event, "teammate")

    mock_chromadb["embed_document"].assert_called_once()
    call_args = mock_chromadb["embed_document"].call_args
    assert f"evt_{sample_event.event_id}" == call_args.kwargs["doc_id"]
    assert "event" == call_args.kwargs["metadata"]["content_type"]
    assert "jira" == call_args.kwargs["metadata"]["source_platform"]


@pytest.mark.asyncio
async def test_router_queries_related_context(db_full, sample_event, mock_llm_router, mock_chromadb):
    """Router calls memory_search for related past events."""
    await _insert_event(db_full, sample_event)

    await run_router(sample_event, "teammate")

    mock_chromadb["memory_search"].assert_called_once()
    call_args = mock_chromadb["memory_search"].call_args
    assert "NPE in PaymentService" in call_args.args[0]


@pytest.mark.asyncio
async def test_entities_stored_in_db(db_full, sample_event, mock_llm_router, mock_chromadb):
    """Router stores extracted entities in the entities table."""
    await _insert_event(db_full, sample_event)

    await run_router(sample_event, "teammate")

    async with db_full.execute("SELECT * FROM entities ORDER BY canonical_name") as cursor:
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
async def test_entity_merge_on_duplicate(db_full, sample_event, mock_chromadb):
    """Entities with same canonical_name get platform_refs merged."""
    await _insert_event(db_full, sample_event)

    # Pre-insert an entity with the same canonical_name but different platform
    await db_full.execute(
        """INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs, link_method, confidence)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("ent_existing", "ticket", "BUG-1234", json.dumps({"bitbucket": ["PR-related-to-BUG-1234"]}), "explicit", 1.0),
    )
    await db_full.commit()

    # Create a RouterOutput with BUG-1234 entity
    router_output = RouterOutput(**MOCK_ROUTER_RESPONSE)
    await _store_entities(sample_event.event_id, router_output)

    # Verify merge
    async with db_full.execute(
        "SELECT platform_refs FROM entities WHERE canonical_name = 'BUG-1234'"
    ) as cursor:
        row = await cursor.fetchone()

    refs = json.loads(row[0])
    assert "bitbucket" in refs  # Original
    assert "jira" in refs  # Merged from router output
