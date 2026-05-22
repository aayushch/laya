# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for group summary pipeline and API endpoints."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

from laya.models.card import GroupSummaryResponse, KeyEvent


@pytest.fixture
def mock_llm_response():
    """A mock LLM response for group summary generation."""
    resp = MagicMock()
    resp.parsed = {
        "headline": "PR #540 reviewed and approved",
        "summary": "Alice opened PR #540 for the auth module refactor. Bob reviewed and approved it after two rounds of feedback. The PR is now ready to merge.",
        "key_events": [
            {"event": "PR #540 opened by Alice", "timestamp": "2026-02-22T10:00:00Z"},
            {"event": "Review requested from Bob", "timestamp": "2026-02-22T14:30:00Z"},
            {"event": "Bob approved after feedback addressed", "timestamp": "2026-02-23T09:15:00Z"},
        ],
        "current_status": "Approved and ready to merge",
        "pending_actions": ["Merge PR before sprint close"],
    }
    resp.model = "claude-haiku-4-5"
    return resp


async def _insert_card(db, card_id: str, entity_id: str, header: str, summary: str, space_id: str = "default"):
    """Helper to insert a test card."""
    await db.execute(
        """INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type,
                actor_name, actor_email, subject_type, subject_id, subject_title,
                content_body, content_metadata, raw_json, space_id)
           VALUES (?, datetime('now'), 'jira', 'issue_updated', 'Alice', 'alice@co.com',
                   'ticket', 'BUG-1234', 'NPE in PaymentService',
                   'test body', '{}', '{}', ?)""",
        (f"evt_{card_id}", space_id),
    )
    await db.execute(
        """INSERT INTO action_cards
               (card_id, event_id, entity_id, header, summary, priority,
                persona, category, status, privacy_tier, space_id,
                created_at, group_active_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'HIGH', 'ENGINEER', 'CODE', 'pending', 2, ?,
                   datetime('now'), datetime('now'), datetime('now'))""",
        (card_id, f"evt_{card_id}", entity_id, header, summary, space_id),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_group_summary_table_exists(db):
    """Migration creates the group_summaries table."""
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='group_summaries'"
    )
    row = rows[0] if rows else None
    assert row is not None


@pytest.mark.asyncio
async def test_initial_generation(db, mock_llm_response):
    """First summary is generated when entity group reaches 2 cards."""
    entity_id = "jira:ticket:BUG-1234"
    await _insert_card(db, "card_1", entity_id, "Bug reported", "NPE in PaymentService")
    await _insert_card(db, "card_2", entity_id, "Bug assigned", "Assigned to Alice for fix")

    no_debounce = {"group_summary_seconds": 0, "daily_summary_seconds": 30, "event_batch_window_seconds": 3, "event_batch_max_size": 10}
    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock, return_value=mock_llm_response), \
         patch("laya.pipeline.group_summary.manager") as mock_ws, \
         patch("laya.pipeline.group_summary.get_debounce_config", return_value=no_debounce):
        mock_ws.broadcast = AsyncMock()

        from laya.pipeline.group_summary import trigger_group_summary_update
        await trigger_group_summary_update(entity_id, "card_2", "default")

    # Verify summary was persisted
    _rows = await db.execute_fetchall(
        "SELECT * FROM group_summaries WHERE entity_id = ?", (entity_id,)
    )
    row = _rows[0] if _rows else None
    assert row is not None
    assert row["headline"] == "PR #540 reviewed and approved"
    assert row["card_count"] == 2
    card_ids = json.loads(row["card_ids"])
    assert "card_1" in card_ids
    assert "card_2" in card_ids


@pytest.mark.asyncio
async def test_rolling_update(db, mock_llm_response):
    """Existing summary is updated when a new card arrives."""
    entity_id = "jira:ticket:BUG-1234"
    await _insert_card(db, "card_1", entity_id, "Bug reported", "NPE found")
    await _insert_card(db, "card_2", entity_id, "Bug assigned", "Assigned to Alice")

    # Insert initial summary
    await db.execute(
        """INSERT INTO group_summaries
               (entity_id, headline, summary, key_events, current_status,
                pending_actions, card_ids, card_count, space_id)
           VALUES (?, 'Bug reported', 'Initial summary', '[{"event": "Bug found", "timestamp": "2026-02-20T08:00:00Z"}]',
                   'Under investigation', '["Fix the bug"]',
                   '["card_1", "card_2"]', 2, 'default')""",
        (entity_id,),
    )
    await db.commit()

    # Add a third card
    await _insert_card(db, "card_3", entity_id, "Bug fixed", "Fix deployed to staging")

    updated_response = MagicMock()
    updated_response.parsed = {
        "headline": "Bug fixed and deployed",
        "summary": "The NPE was found, assigned to Alice, and fixed. Deployed to staging.",
        "key_events": [
            {"event": "Bug found", "timestamp": "2026-02-20T08:00:00Z"},
            {"event": "Assigned to Alice", "timestamp": "2026-02-20T10:00:00Z"},
            {"event": "Fix deployed", "timestamp": "2026-02-21T16:00:00Z"},
        ],
        "current_status": "Fix deployed to staging",
        "pending_actions": None,
    }
    updated_response.model = "claude-haiku-4-5"

    no_debounce = {"group_summary_seconds": 0, "daily_summary_seconds": 30, "event_batch_window_seconds": 3, "event_batch_max_size": 10}
    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock, return_value=updated_response), \
         patch("laya.pipeline.group_summary.manager") as mock_ws, \
         patch("laya.pipeline.group_summary.get_debounce_config", return_value=no_debounce):
        mock_ws.broadcast = AsyncMock()

        from laya.pipeline.group_summary import trigger_group_summary_update
        await trigger_group_summary_update(entity_id, "card_3", "default")

    _rows = await db.execute_fetchall(
        "SELECT * FROM group_summaries WHERE entity_id = ?", (entity_id,)
    )
    row = _rows[0] if _rows else None
    assert row["headline"] == "Bug fixed and deployed"
    assert row["card_count"] == 3
    card_ids = json.loads(row["card_ids"])
    assert "card_3" in card_ids


@pytest.mark.asyncio
async def test_no_summary_for_single_card(db, mock_llm_response):
    """Summary is not generated for groups with only 1 card."""
    entity_id = "jira:ticket:SOLO-1"
    await _insert_card(db, "solo_card", entity_id, "Single card", "Just one event")

    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock, return_value=mock_llm_response) as mock_llm, \
         patch("laya.pipeline.group_summary.manager"):

        from laya.pipeline.group_summary import trigger_group_summary_update
        await trigger_group_summary_update(entity_id, "solo_card", "default")

    # LLM should NOT have been called
    mock_llm.assert_not_called()

    _rows = await db.execute_fetchall(
        "SELECT * FROM group_summaries WHERE entity_id = ?", (entity_id,)
    )
    row = _rows[0] if _rows else None
    assert row is None


@pytest.mark.asyncio
async def test_disabled_setting(db, mock_llm_response):
    """Summary is not generated when feature is disabled."""
    entity_id = "jira:ticket:BUG-OFF"
    await _insert_card(db, "off_1", entity_id, "Card 1", "First card")
    await _insert_card(db, "off_2", entity_id, "Card 2", "Second card")

    disabled_settings = {"group_summaries": {"enabled": False}}

    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock) as mock_llm, \
         patch("laya.pipeline.group_summary.load_settings", return_value=disabled_settings), \
         patch("laya.pipeline.group_summary.manager"):

        from laya.pipeline.group_summary import trigger_group_summary_update
        await trigger_group_summary_update(entity_id, "off_2", "default")

    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_regenerate_group_summary(db, mock_llm_response):
    """Regeneration creates summary from scratch."""
    entity_id = "jira:ticket:BUG-REGEN"
    await _insert_card(db, "regen_1", entity_id, "Card 1", "First event")
    await _insert_card(db, "regen_2", entity_id, "Card 2", "Second event")
    await _insert_card(db, "regen_3", entity_id, "Card 3", "Third event")

    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock, return_value=mock_llm_response), \
         patch("laya.pipeline.group_summary.manager") as mock_ws:
        mock_ws.broadcast = AsyncMock()

        from laya.pipeline.group_summary import regenerate_group_summary
        result = await regenerate_group_summary(entity_id)

    assert result is not None
    assert result["headline"] == "PR #540 reviewed and approved"
    assert result["card_count"] == 3


@pytest.mark.asyncio
async def test_websocket_broadcast(db, mock_llm_response):
    """Summary generation broadcasts via WebSocket."""
    entity_id = "jira:ticket:BUG-WS"
    await _insert_card(db, "ws_1", entity_id, "Card 1", "First")
    await _insert_card(db, "ws_2", entity_id, "Card 2", "Second")

    no_debounce = {"group_summary_seconds": 0, "daily_summary_seconds": 30, "event_batch_window_seconds": 3, "event_batch_max_size": 10}
    with patch("laya.pipeline.group_summary.llm_call", new_callable=AsyncMock, return_value=mock_llm_response), \
         patch("laya.pipeline.group_summary.manager") as mock_ws, \
         patch("laya.pipeline.group_summary.get_debounce_config", return_value=no_debounce):
        mock_ws.broadcast = AsyncMock()

        from laya.pipeline.group_summary import trigger_group_summary_update
        await trigger_group_summary_update(entity_id, "ws_2", "default")

    mock_ws.broadcast.assert_called_once()
    call_args = mock_ws.broadcast.call_args[0][0]
    assert call_args["type"] == "group_summary_updated"
    assert call_args["entity_id"] == entity_id
    assert "summary" in call_args


@pytest.mark.asyncio
async def test_grouped_cards_include_summary(db, mock_llm_response):
    """GET /cards/grouped includes group_summary in response."""
    entity_id = "jira:ticket:BUG-GROUPED"
    await _insert_card(db, "grp_1", entity_id, "Card 1", "First")
    await _insert_card(db, "grp_2", entity_id, "Card 2", "Second")

    # Insert a summary
    await db.execute(
        """INSERT INTO group_summaries
               (entity_id, headline, summary, key_events, current_status,
                pending_actions, card_ids, card_count, space_id)
           VALUES (?, 'Test headline', 'Test summary', '[{"event": "Event 1", "timestamp": "2026-02-20T08:00:00Z"}]',
                   'In progress', null, '["grp_1", "grp_2"]', 2, 'default')""",
        (entity_id,),
    )
    await db.commit()

    with patch("laya.config.load_settings", return_value={
        "smart_grouping": {"smart_display": False},
        "group_summaries": {"enabled": True},
    }):
        from laya.api.cards_api import get_grouped_cards
        result = await get_grouped_cards()

    assert len(result.groups) >= 1
    group = next((g for g in result.groups if g.entity_id == entity_id), None)
    assert group is not None
    assert group.group_summary is not None
    assert group.group_summary.headline == "Test headline"
    assert group.group_summary.card_count == 2


def test_legacy_string_key_events():
    """Old string[] key_events still deserialize correctly."""
    resp = GroupSummaryResponse(
        entity_id="test:legacy",
        headline="Test",
        summary="Test summary",
        key_events=["Old format event 1", "Old format event 2"],
        current_status="Active",
        pending_actions=None,
        card_count=2,
        card_ids=["c1", "c2"],
    )
    assert resp.key_events is not None
    assert isinstance(resp.key_events[0], str)


def test_new_object_key_events():
    """New object format key_events deserialize correctly."""
    resp = GroupSummaryResponse(
        entity_id="test:new",
        headline="Test",
        summary="Test summary",
        key_events=[
            {"event": "Something happened", "timestamp": "2026-05-04T04:23:03Z"},
        ],
        current_status="Active",
        pending_actions=None,
        card_count=2,
        card_ids=["c1", "c2"],
    )
    assert resp.key_events is not None
    assert isinstance(resp.key_events[0], KeyEvent)
    assert resp.key_events[0].event == "Something happened"
    assert resp.key_events[0].timestamp == "2026-05-04T04:23:03Z"


def test_key_event_empty_timestamp():
    """KeyEvent with empty timestamp is accepted."""
    resp = GroupSummaryResponse(
        entity_id="test:empty-ts",
        headline="Test",
        summary="Test summary",
        key_events=[
            {"event": "No timestamp available", "timestamp": ""},
        ],
        current_status="Active",
        pending_actions=None,
        card_count=2,
        card_ids=["c1", "c2"],
    )
    assert resp.key_events is not None
    assert isinstance(resp.key_events[0], KeyEvent)
    assert resp.key_events[0].timestamp == ""


def test_mixed_key_events():
    """Mix of old strings and new objects in key_events."""
    resp = GroupSummaryResponse(
        entity_id="test:mixed",
        headline="Test",
        summary="Test summary",
        key_events=[
            "Legacy event text",
            {"event": "New format event", "timestamp": "2026-05-04T04:23:03Z"},
        ],
        current_status="Active",
        pending_actions=None,
        card_count=2,
        card_ids=["c1", "c2"],
    )
    assert resp.key_events is not None
    assert isinstance(resp.key_events[0], str)
    assert isinstance(resp.key_events[1], KeyEvent)
