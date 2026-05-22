# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent session manager."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.agents import session_manager
from laya.models.workspace import (
    AgentType,
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
)
from tests.conftest import insert_test_card, insert_test_event


@pytest.mark.asyncio
class TestSessionManager:
    async def test_start_session_creates_db_row(self, db):
        """start_session inserts a row into workspace_sessions."""
        await insert_test_card(db, card_id="card_test", event_id="evt_start")

        mock_agent = MagicMock()
        mock_agent.start_session = AsyncMock()

        with patch("laya.agents.session_manager._create_agent", return_value=mock_agent):
            with patch("laya.agents.session_manager.get_configured_agent_type", return_value=AgentType.CLAUDE_CODE):
                session_id, agent = await session_manager.start_session(
                    card_id="card_test",
                    prompt="Fix the bug",
                    repo_path="/tmp/repo",
                )

        assert session_id.startswith("sess_")
        assert agent is mock_agent

        # Verify DB row
        async with db.execute(
            "SELECT card_id, agent_type, status FROM workspace_sessions WHERE session_id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["card_id"] == "card_test"
        assert row["agent_type"] == "claude_code"
        assert row["status"] == "running"

        # Clean up
        session_manager._active_sessions.pop(session_id, None)

    async def test_complete_session_stores_findings(self, db):
        """complete_session updates findings and status."""
        await insert_test_card(db, card_id="card_complete", event_id="evt_complete")

        await db.execute(
            "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status) VALUES (?, ?, ?, ?)",
            ("sess_test_complete", "card_complete", "claude_code", "running"),
        )
        await db.commit()

        findings = {"agent_result": "Fixed NPE", "files_changed": 2}
        await session_manager.complete_session("sess_test_complete", findings=findings)

        async with db.execute(
            "SELECT status, findings_json FROM workspace_sessions WHERE session_id = ?",
            ("sess_test_complete",),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["status"] == "completed"
        stored_findings = json.loads(row["findings_json"])
        assert stored_findings["files_changed"] == 2

    async def test_complete_session_with_error(self, db):
        """complete_session with error sets status to failed."""
        await insert_test_card(db, card_id="card_error", event_id="evt_error")

        await db.execute(
            "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status) VALUES (?, ?, ?, ?)",
            ("sess_test_error", "card_error", "claude_code", "running"),
        )
        await db.commit()

        await session_manager.complete_session("sess_test_error", error="Agent crashed")

        async with db.execute(
            "SELECT status, error_message FROM workspace_sessions WHERE session_id = ?",
            ("sess_test_error",),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["status"] == "failed"
        assert row["error_message"] == "Agent crashed"

    async def test_store_workspace_event(self, db):
        """store_workspace_event persists event to SQLite."""
        await insert_test_card(db, card_id="card_evt", event_id="evt_evt")

        await db.execute(
            "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status) VALUES (?, ?, ?, ?)",
            ("sess_evt", "card_evt", "claude_code", "running"),
        )
        await db.commit()

        event = WorkspaceEvent(
            event_id="we_test_001",
            session_id="sess_evt",
            event_type=WorkspaceEventType.AGENT_MESSAGE,
            actor=WorkspaceEventActor.AGENT,
            content={"text": "Reading file..."},
        )
        await session_manager.store_workspace_event(event)

        async with db.execute(
            "SELECT event_type, actor, content FROM workspace_events WHERE event_id = ?",
            ("we_test_001",),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["event_type"] == "agent_message"
        assert row["actor"] == "agent"
        content = json.loads(row["content"])
        assert content["text"] == "Reading file..."

    async def test_get_configured_agent_type(self):
        """get_configured_agent_type reads from settings."""
        with patch("laya.agents.session_manager.load_settings", return_value={"coding_agent": "gemini_cli"}):
            agent_type = session_manager.get_configured_agent_type()

        assert agent_type == AgentType.GEMINI_CLI

    async def test_get_configured_agent_type_default(self):
        """get_configured_agent_type defaults to claude_code."""
        with patch("laya.agents.session_manager.load_settings", return_value={}):
            agent_type = session_manager.get_configured_agent_type()

        assert agent_type == AgentType.CLAUDE_CODE

    async def test_cancel_session(self, db):
        """cancel_session updates status and removes from active."""
        await insert_test_card(db, card_id="card_cancel", event_id="evt_cancel")

        await db.execute(
            "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status) VALUES (?, ?, ?, ?)",
            ("sess_cancel", "card_cancel", "claude_code", "running"),
        )
        await db.commit()

        mock_agent = MagicMock()
        mock_agent.cancel = AsyncMock()
        session_manager._active_sessions["sess_cancel"] = mock_agent

        await session_manager.cancel_session("sess_cancel")

        async with db.execute(
            "SELECT status FROM workspace_sessions WHERE session_id = ?",
            ("sess_cancel",),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["status"] == "cancelled"
        assert "sess_cancel" not in session_manager._active_sessions

    async def test_pause_resume_session(self, db):
        """pause_session and resume_session update status."""
        await insert_test_card(db, card_id="card_pause", event_id="evt_pause")

        await db.execute(
            "INSERT INTO workspace_sessions (session_id, card_id, agent_type, status) VALUES (?, ?, ?, ?)",
            ("sess_pause", "card_pause", "claude_code", "running"),
        )
        await db.commit()

        mock_agent = MagicMock()
        mock_agent.pause = AsyncMock()
        mock_agent.resume = AsyncMock()
        session_manager._active_sessions["sess_pause"] = mock_agent

        await session_manager.pause_session("sess_pause")

        async with db.execute(
            "SELECT status FROM workspace_sessions WHERE session_id = ?", ("sess_pause",)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["status"] == "paused"

        await session_manager.resume_session("sess_pause")

        async with db.execute(
            "SELECT status FROM workspace_sessions WHERE session_id = ?", ("sess_pause",)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["status"] == "running"

        # Clean up
        session_manager._active_sessions.pop("sess_pause", None)
