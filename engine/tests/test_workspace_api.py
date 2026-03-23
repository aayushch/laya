"""Tests for the workspace REST API."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card, insert_test_event


async def _setup_workspace(db):
    """Insert parent chain + session + events for testing."""
    # Insert event with router_output
    router_output = json.dumps({
        "entities": [{"entity_type": "ticket", "value": "BUG-1234"}],
        "research_plan": ["Check git blame"],
    })
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, actor_name, actor_email,
            content_body, raw_json, router_output, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("evt_ws_test", "2026-02-22T14:30:00Z", "jira", "issue_assigned",
         "ticket", "BUG-1234", "NPE Test", "Sarah", "sarah@co.com",
         "NullPointerException", "{}", router_output, None),
    )
    # Insert action_card with all required columns
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            entity_id, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("card_ws", "evt_ws_test", "HIGH", "ENGINEER", "CODE",
         "Test Card", "Test summary", "jira:ticket:BUG-1234", None),
    )
    # Insert session with add_dirs column
    await db.execute(
        """INSERT INTO workspace_sessions
           (session_id, card_id, agent_type, status, repo_path, add_dirs)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("sess_ws_test", "card_ws", "claude_code", "running", "/tmp/repo", None),
    )
    # Insert events with agent_message_id column
    await db.execute(
        """INSERT INTO workspace_events
           (event_id, session_id, event_type, actor, content, requires_input, agent_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("we_001", "sess_ws_test", "status_change", "system",
         json.dumps({"status": "started"}), False, None),
    )
    await db.execute(
        """INSERT INTO workspace_events
           (event_id, session_id, event_type, actor, content, requires_input, agent_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("we_002", "sess_ws_test", "file_read", "agent",
         json.dumps({"file": "Payment.java"}), False, None),
    )
    await db.commit()


@pytest.mark.asyncio
class TestWorkspaceAPI:
    async def test_get_workspace_with_session(self, db):
        """GET /cards/:card_id/workspace returns session + events."""
        await _setup_workspace(db)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_ws/workspace")

        assert resp.status_code == 200
        data = resp.json()
        assert data["card_id"] == "card_ws"
        assert data["session"]["session_id"] == "sess_ws_test"
        assert data["session"]["agent_type"] == "claude_code"
        assert data["session"]["status"] == "running"
        assert len(data["events"]) == 2
        assert data["events"][0]["event_type"] == "status_change"
        assert data["events"][1]["event_type"] == "file_read"

    async def test_get_workspace_empty(self, db):
        """GET /cards/:card_id/workspace returns empty when no session."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/nonexistent/workspace")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session"] is None
        assert data["events"] == []

    async def test_get_workspace_context(self, db):
        """GET /cards/:card_id/workspace includes context from router output."""
        await _setup_workspace(db)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_ws/workspace")

        data = resp.json()
        assert "related_entities" in data["context"]
        assert data["context"]["related_entities"][0]["value"] == "BUG-1234"
        assert "research_plan" in data["context"]
