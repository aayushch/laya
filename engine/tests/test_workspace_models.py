"""Tests for workspace Pydantic models and enums."""

from datetime import datetime

import pytest

from laya.models.workspace import (
    AgentType,
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
    WorkspaceSession,
)


class TestEnums:
    def test_agent_types(self):
        assert AgentType.CLAUDE_CODE.value == "claude_code"
        assert AgentType.GEMINI_CLI.value == "gemini_cli"
        assert AgentType.CODEX_CLI.value == "codex_cli"

    def test_session_statuses(self):
        assert SessionStatus.STARTING.value == "starting"
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.AWAITING_INPUT.value == "awaiting_input"
        assert SessionStatus.PAUSED.value == "paused"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.FAILED.value == "failed"
        assert SessionStatus.CANCELLED.value == "cancelled"

    def test_workspace_event_types(self):
        assert WorkspaceEventType.AGENT_MESSAGE.value == "agent_message"
        assert WorkspaceEventType.APPROVAL_REQUEST.value == "approval_request"
        assert WorkspaceEventType.FILE_WRITE.value == "file_write"
        assert len(WorkspaceEventType) == 9

    def test_workspace_event_actors(self):
        assert WorkspaceEventActor.AGENT.value == "agent"
        assert WorkspaceEventActor.USER.value == "user"
        assert WorkspaceEventActor.SYSTEM.value == "system"

    def test_agent_type_from_string(self):
        assert AgentType("claude_code") == AgentType.CLAUDE_CODE

    def test_invalid_agent_type_raises(self):
        with pytest.raises(ValueError):
            AgentType("invalid_agent")


class TestWorkspaceSession:
    def test_create_minimal(self):
        session = WorkspaceSession(
            session_id="sess_abc",
            card_id="card_001",
            agent_type=AgentType.CLAUDE_CODE,
        )
        assert session.status == SessionStatus.STARTING
        assert session.repo_path is None
        assert session.findings_json is None

    def test_create_full(self):
        session = WorkspaceSession(
            session_id="sess_abc",
            card_id="card_001",
            agent_type=AgentType.GEMINI_CLI,
            status=SessionStatus.RUNNING,
            repo_path="/home/user/repos/test",
            initial_prompt="Fix the NPE",
            findings_json={"agent_result": "Fixed"},
        )
        assert session.agent_type == AgentType.GEMINI_CLI
        assert session.status == SessionStatus.RUNNING
        assert session.findings_json["agent_result"] == "Fixed"


class TestWorkspaceEvent:
    def test_create_agent_message(self):
        event = WorkspaceEvent(
            event_id="we_001",
            session_id="sess_abc",
            event_type=WorkspaceEventType.AGENT_MESSAGE,
            actor=WorkspaceEventActor.AGENT,
            content={"text": "Looking at PaymentService.java"},
        )
        assert event.requires_input is False
        assert event.content["text"] == "Looking at PaymentService.java"

    def test_create_approval_request(self):
        event = WorkspaceEvent(
            event_id="we_002",
            session_id="sess_abc",
            event_type=WorkspaceEventType.APPROVAL_REQUEST,
            actor=WorkspaceEventActor.AGENT,
            content={"message": "Modify 3 files?"},
            requires_input=True,
        )
        assert event.requires_input is True

    def test_default_content_is_empty_dict(self):
        event = WorkspaceEvent(
            event_id="we_003",
            session_id="sess_abc",
            event_type=WorkspaceEventType.STATUS_CHANGE,
            actor=WorkspaceEventActor.SYSTEM,
        )
        assert event.content == {}
