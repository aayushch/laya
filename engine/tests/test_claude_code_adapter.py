# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

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
    """_parse_stream_json now returns a list of WorkspaceEvent."""

    def test_assistant_text_block(self, agent):
        """Parses assistant message with text content block."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_01",
                "content": [{"type": "text", "text": "Looking at the code"}],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.AGENT_MESSAGE

    def test_assistant_tool_use_read(self, agent):
        """Parses assistant message with Read tool_use block as FILE_READ."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_02",
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/src/main.py"}}],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.FILE_READ

    def test_assistant_tool_use_write(self, agent):
        """Parses assistant message with Write tool_use block as FILE_WRITE."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_03",
                "content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "/src/fix.py"}}],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.FILE_WRITE

    def test_assistant_tool_use_edit(self, agent):
        """Parses assistant message with Edit tool_use block as FILE_WRITE."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_04",
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/src/fix.py"}}],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.FILE_WRITE

    def test_assistant_tool_use_bash(self, agent):
        """Parses assistant message with Bash tool_use block as TOOL_CALL."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_05",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.TOOL_CALL

    def test_assistant_multiple_blocks(self, agent):
        """Assistant message with multiple content blocks yields multiple events."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_06",
                "content": [
                    {"type": "text", "text": "Let me check"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/src/a.py"}},
                ],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 2
        assert events[0].event_type == WorkspaceEventType.AGENT_MESSAGE
        assert events[1].event_type == WorkspaceEventType.FILE_READ

    def test_result_type(self, agent):
        """Parses result type as STATUS_CHANGE."""
        line = json.dumps({"type": "result", "result": "All fixed"})
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.STATUS_CHANGE
        assert events[0].content.get("status") == "result_received"

    def test_system_init_captures_session_id(self, agent):
        """system.init message captures cc_session_id."""
        line = json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "cc-uuid-1234",
            "cwd": "/repo",
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.STATUS_CHANGE
        assert agent.cc_session_id == "cc-uuid-1234"

    def test_invalid_json_returns_empty(self, agent):
        """Non-JSON lines return empty list."""
        events = agent._parse_stream_json("this is not json")
        assert events == []

    def test_unknown_type_returns_empty(self, agent):
        """Unknown message types return empty list."""
        line = json.dumps({"type": "rate_limit_event", "data": "loading"})
        events = agent._parse_stream_json(line)
        assert events == []

    def test_ask_user_question(self, agent):
        """AskUserQuestion tool_use yields APPROVAL_REQUEST with requires_input."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_07",
                "content": [{
                    "type": "tool_use",
                    "name": "AskUserQuestion",
                    "input": {"questions": ["Should I continue?"]},
                }],
            },
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.APPROVAL_REQUEST
        assert events[0].requires_input is True


class TestApprovalDetection:
    """_is_approval_prompt is currently disabled (always returns False)."""

    def test_always_returns_false(self, agent):
        """Approval detection is disabled in plan mode."""
        assert not agent._is_approval_prompt("Do you want to proceed? [Y/n]")
        assert not agent._is_approval_prompt("Shall I continue? (yes/no)")
        assert not agent._is_approval_prompt("Do you approve this change?")
        assert not agent._is_approval_prompt("Looking at the PaymentService class")
        assert not agent._is_approval_prompt("")
