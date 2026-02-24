"""Tests for the Claude Code agent adapter."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.agents.claude_code import ClaudeCodeAgent
from laya.models.workspace import WorkspaceEventType


@pytest.fixture
def agent():
    """Create a ClaudeCodeAgent with session_id pre-set for testing."""
    a = ClaudeCodeAgent()
    a._session_id = "sess_test"
    return a


class TestParseStreamJson:
    def test_assistant_message(self, agent):
        """Parses assistant message type."""
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking at the code"}]}})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.AGENT_MESSAGE

    def test_tool_use_read(self, agent):
        """Parses tool_use with Read tool as FILE_READ."""
        line = json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "/src/main.py"}})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.FILE_READ

    def test_tool_use_write(self, agent):
        """Parses tool_use with Write tool as FILE_WRITE."""
        line = json.dumps({"type": "tool_use", "name": "Write", "input": {"file_path": "/src/fix.py"}})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.FILE_WRITE

    def test_tool_use_edit(self, agent):
        """Parses tool_use with Edit tool as FILE_WRITE."""
        line = json.dumps({"type": "tool_use", "name": "Edit", "input": {"file_path": "/src/fix.py"}})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.FILE_WRITE

    def test_tool_use_other(self, agent):
        """Parses tool_use with unknown tool as TOOL_CALL."""
        line = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.TOOL_CALL

    def test_result_type(self, agent):
        """Parses result type as STATUS_CHANGE."""
        line = json.dumps({"type": "result", "result": "All fixed"})
        event = agent._parse_stream_json(line)
        assert event is not None
        assert event.event_type == WorkspaceEventType.STATUS_CHANGE
        assert event.content.get("status") == "result_received"

    def test_invalid_json_returns_none(self, agent):
        """Non-JSON lines return None."""
        event = agent._parse_stream_json("this is not json")
        assert event is None

    def test_unknown_type_returns_none(self, agent):
        """Unknown message types return None."""
        line = json.dumps({"type": "system", "data": "loading"})
        event = agent._parse_stream_json(line)
        assert event is None


class TestApprovalDetection:
    def test_detects_yn_prompt(self, agent):
        assert agent._is_approval_prompt("Do you want to proceed? [Y/n]")

    def test_detects_yes_no(self, agent):
        assert agent._is_approval_prompt("Shall I continue? (yes/no)")

    def test_detects_approve_deny(self, agent):
        assert agent._is_approval_prompt("Do you approve this change?")

    def test_normal_text_not_detected(self, agent):
        assert not agent._is_approval_prompt("Looking at the PaymentService class")

    def test_empty_string(self, agent):
        assert not agent._is_approval_prompt("")
