# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the workspace REST API."""

import json
from pathlib import Path
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


async def _setup_research_session(db, research_root: Path, card_id="card_research"):
    """Insert a research session whose repo_path is a dir under research_root.

    Returns the session directory (which holds a sample report.md).
    """
    session_dir = research_root / "sess_research"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "report.md").write_text("# Findings\nhello world\n", encoding="utf-8")

    await insert_test_card(db, card_id=card_id, category="RESEARCH")
    await db.execute(
        """INSERT INTO workspace_sessions
           (session_id, card_id, agent_type, status, repo_path, add_dirs, session_type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("sess_research", card_id, "claude_code", "completed", str(session_dir), None, "research"),
    )
    await db.commit()
    return session_dir


@pytest.mark.asyncio
class TestResearchFilePathSafety:
    """Path-traversal protection on the research-file browse/read endpoints."""

    async def _client(self):
        from laya.main import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_read_legitimate_file(self, db, tmp_path):
        root = tmp_path / "research"
        await _setup_research_session(db, root)
        with patch("laya.api.workspace_api._RESEARCH_ROOT", str(root.resolve())):
            async with await self._client() as client:
                resp = await client.get(
                    "/workspace/research-files/card_research/read",
                    params={"path": "report.md"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "report.md"
        assert "hello world" in body["content"]

    async def test_list_files(self, db, tmp_path):
        root = tmp_path / "research"
        await _setup_research_session(db, root)
        with patch("laya.api.workspace_api._RESEARCH_ROOT", str(root.resolve())):
            async with await self._client() as client:
                resp = await client.get("/workspace/research-files/card_research")
        assert resp.status_code == 200
        names = [f["name"] for f in resp.json()["files"]]
        assert "report.md" in names

    @pytest.mark.parametrize(
        "evil_path",
        [
            "../../../../../../etc/passwd",       # relative traversal
            "/etc/passwd",                         # absolute-path override
            "sess_research/../../../etc/passwd",   # traversal after a valid prefix
        ],
    )
    async def test_read_traversal_blocked(self, db, tmp_path, evil_path):
        root = tmp_path / "research"
        await _setup_research_session(db, root)
        # A real file outside the root that an escape could otherwise reach.
        outside = tmp_path / "secret.txt"
        outside.write_text("TOP SECRET", encoding="utf-8")
        with patch("laya.api.workspace_api._RESEARCH_ROOT", str(root.resolve())):
            async with await self._client() as client:
                resp = await client.get(
                    "/workspace/research-files/card_research/read",
                    params={"path": evil_path},
                )
        assert resp.status_code == 403
        assert "TOP SECRET" not in resp.text

    async def test_sibling_root_prefix_not_matched(self, db, tmp_path):
        """A session dir in a sibling like '<root>-evil' must not be treated
        as inside the research root (regression for the missing-separator bug
        in the old startswith check)."""
        root = tmp_path / "research"
        root.mkdir(parents=True, exist_ok=True)
        sibling = tmp_path / "research-evil"
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "leak.md").write_text("leak", encoding="utf-8")

        await insert_test_card(db, card_id="card_sibling", category="RESEARCH")
        await db.execute(
            """INSERT INTO workspace_sessions
               (session_id, card_id, agent_type, status, repo_path, add_dirs, session_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("sess_sibling", "card_sibling", "claude_code", "completed", str(sibling), None, "research"),
        )
        await db.commit()

        with patch("laya.api.workspace_api._RESEARCH_ROOT", str(root.resolve())):
            async with await self._client() as client:
                # List returns empty (sibling not selected as a research dir).
                list_resp = await client.get("/workspace/research-files/card_sibling")
                read_resp = await client.get(
                    "/workspace/research-files/card_sibling/read",
                    params={"path": "leak.md"},
                )
        assert list_resp.status_code == 200
        assert list_resp.json()["files"] == []
        # No research dir selected -> 404 (not a 200 read of the sibling file).
        assert read_resp.status_code == 404
