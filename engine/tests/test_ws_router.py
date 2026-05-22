# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for WebSocket message router."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from laya.api.ws_router import handle_ws_message


@pytest.fixture
def mock_sm():
    """Mock session_manager for WS router tests."""
    with patch("laya.api.ws_router.session_manager") as mock:
        mock.send_input = AsyncMock()
        mock.pause_session = AsyncMock()
        mock.resume_session = AsyncMock()
        mock.cancel_session = AsyncMock()
        mock.store_workspace_event = AsyncMock()
        yield mock


@pytest.mark.asyncio
class TestWsRouter:
    async def test_approve_action(self, mock_sm):
        """approve_action sends 'yes' to agent and stores event."""
        msg = json.dumps({
            "type": "approve_action",
            "session_id": "sess_001",
            "payload": {"approved": True},
        })
        await handle_ws_message(msg)

        mock_sm.send_input.assert_called_once_with("sess_001", "yes")
        mock_sm.store_workspace_event.assert_called_once()

    async def test_deny_action(self, mock_sm):
        """deny_action sends reason to agent."""
        msg = json.dumps({
            "type": "deny_action",
            "session_id": "sess_001",
            "payload": {"reason": "Skip test file"},
        })
        await handle_ws_message(msg)

        mock_sm.send_input.assert_called_once_with("sess_001", "Skip test file")
        mock_sm.store_workspace_event.assert_called_once()

    async def test_deny_action_default_reason(self, mock_sm):
        """deny_action with no reason sends 'no'."""
        msg = json.dumps({
            "type": "deny_action",
            "session_id": "sess_002",
            "payload": {},
        })
        await handle_ws_message(msg)

        mock_sm.send_input.assert_called_once_with("sess_002", "no")

    async def test_user_input(self, mock_sm):
        """user_input sends message text to agent."""
        msg = json.dumps({
            "type": "user_input",
            "session_id": "sess_001",
            "payload": {"message": "Also check CustomerDAO"},
        })
        await handle_ws_message(msg)

        mock_sm.send_input.assert_called_once_with("sess_001", "Also check CustomerDAO")
        mock_sm.store_workspace_event.assert_called_once()

    async def test_session_control_pause(self, mock_sm):
        """session_control with action=pause calls pause_session."""
        msg = json.dumps({
            "type": "session_control",
            "session_id": "sess_001",
            "payload": {"action": "pause"},
        })
        await handle_ws_message(msg)

        mock_sm.pause_session.assert_called_once_with("sess_001")

    async def test_session_control_resume(self, mock_sm):
        """session_control with action=resume calls resume_session."""
        msg = json.dumps({
            "type": "session_control",
            "session_id": "sess_001",
            "payload": {"action": "resume"},
        })
        await handle_ws_message(msg)

        mock_sm.resume_session.assert_called_once_with("sess_001")

    async def test_session_control_cancel(self, mock_sm):
        """session_control with action=cancel calls cancel_session."""
        msg = json.dumps({
            "type": "session_control",
            "session_id": "sess_001",
            "payload": {"action": "cancel"},
        })
        await handle_ws_message(msg)

        mock_sm.cancel_session.assert_called_once_with("sess_001")

    async def test_invalid_json(self, mock_sm):
        """Invalid JSON is handled gracefully."""
        await handle_ws_message("not valid json {{")
        mock_sm.send_input.assert_not_called()

    async def test_missing_type(self, mock_sm):
        """Message without type is handled gracefully."""
        msg = json.dumps({"session_id": "sess_001"})
        await handle_ws_message(msg)
        mock_sm.send_input.assert_not_called()

    async def test_unknown_type(self, mock_sm):
        """Unknown type is silently ignored."""
        msg = json.dumps({"type": "some_unknown_type", "session_id": "sess_001"})
        await handle_ws_message(msg)
        mock_sm.send_input.assert_not_called()

    async def test_approve_no_session_id(self, mock_sm):
        """approve_action without session_id does nothing."""
        msg = json.dumps({"type": "approve_action", "payload": {}})
        await handle_ws_message(msg)
        mock_sm.send_input.assert_not_called()
