"""Tests for the workspace REST API."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _setup_workspace(db):
    """Insert parent chain + session + events for testing."""
    # Insert event with router_output
    router_output = json.dumps({
        "entities": [{"entity_type": "ticket", "value": "BUG-1234"}],
        "research_plan": ["Check git blame"],
    })
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json, router_output) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("evt_ws_test", "2026-02-22T14:30:00Z", "jira", "issue_assigned",
         "ticket", "BUG-1234", "NPE Test", "{}", router_output),
    )
    # Insert action_card
    await db.execute(
        "INSERT INTO action_cards (card_id, event_id, priority, persona, category, header, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("card_ws", "evt_ws_test", "HIGH", "ENGINEER", "CODE", "Test Card", "Test summary"),
    )
    # Insert session
    await db.execute(
        "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status, repo_path) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_ws_test", "card_ws", "claude_code", "running", "/tmp/repo"),
    )
    # Insert events
    await db.execute(
        "INSERT INTO workspace_events (event_id, session_id, event_type, actor, content, requires_input) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("we_001", "sess_ws_test", "status_change", "system", json.dumps({"status": "started"}), False),
    )
    await db.execute(
        "INSERT INTO workspace_events (event_id, session_id, event_type, actor, content, requires_input) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("we_002", "sess_ws_test", "file_read", "agent", json.dumps({"file": "Payment.java"}), False),
    )
    await db.commit()


@pytest.mark.asyncio
class TestWorkspaceAPI:
    async def test_get_workspace_with_session(self, db_m4):
        """GET /cards/:card_id/workspace returns session + events."""
        await _setup_workspace(db_m4)

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

    async def test_get_workspace_empty(self, db_m4):
        """GET /cards/:card_id/workspace returns empty when no session."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/nonexistent/workspace")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session"] is None
        assert data["events"] == []

    async def test_get_workspace_context(self, db_m4):
        """GET /cards/:card_id/workspace includes context from router output."""
        await _setup_workspace(db_m4)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_ws/workspace")

        data = resp.json()
        assert "related_entities" in data["context"]
        assert data["context"]["related_entities"][0]["value"] == "BUG-1234"
        assert "research_plan" in data["context"]
