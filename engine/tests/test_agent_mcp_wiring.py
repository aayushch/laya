# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MCP callback wiring in spawned CLI agents.

The in-app Claude Code spawner connects to the same HTTP/SSE MCP endpoint
that external clients use. These tests assert that the per-spawn config dict
points at that endpoint with the user's current bearer token, and that the
`--allowedTools` flags derived from the user's enabled MCP scopes are passed
through to the `claude -p` command line.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from laya.agents.claude_code import ClaudeCodeAgent
from laya.agents.mcp_config import (
    MCP_PROMPT_HINT,
    build_laya_mcp_config,
    build_laya_mcp_config_json,
    laya_allowed_tool_flags,
)
from laya.config import ENGINE_HOST, ENGINE_PORT, load_settings, save_settings
from laya.security.keychain import delete_mcp_token, store_mcp_token


@pytest.fixture
def reset_mcp_settings():
    """Snapshot and restore the user's MCP settings + keychain token around each test."""
    settings = load_settings()
    original_mcp = settings.get("mcp")
    yield
    s = load_settings()
    if original_mcp is None:
        s.pop("mcp", None)
    else:
        s["mcp"] = original_mcp
    save_settings(s)
    delete_mcp_token()


class TestMcpConfigBuilder:
    def test_config_uses_http_sse_url(self, reset_mcp_settings):
        cfg = build_laya_mcp_config(space_id=None)
        laya = cfg["mcpServers"]["laya"]
        assert laya["type"] == "sse"
        assert laya["url"] == f"http://{ENGINE_HOST}:{ENGINE_PORT}/mcp/sse"

    def test_space_id_becomes_query_param(self, reset_mcp_settings):
        cfg = build_laya_mcp_config(space_id="space_abc")
        assert cfg["mcpServers"]["laya"]["url"].endswith("?space_id=space_abc")

    def test_bearer_token_in_headers_when_auth_bearer(self, reset_mcp_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "bearer"
        save_settings(s)
        store_mcp_token("lyat_test_xyz")
        cfg = build_laya_mcp_config(space_id=None)
        assert cfg["mcpServers"]["laya"]["headers"] == {"Authorization": "Bearer lyat_test_xyz"}

    def test_no_auth_header_when_auth_none(self, reset_mcp_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "none"
        save_settings(s)
        cfg = build_laya_mcp_config(space_id=None)
        assert cfg["mcpServers"]["laya"]["headers"] == {}

    def test_no_auth_header_when_bearer_but_no_token(self, reset_mcp_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "bearer"
        save_settings(s)
        delete_mcp_token()
        cfg = build_laya_mcp_config(space_id=None)
        assert cfg["mcpServers"]["laya"]["headers"] == {}

    def test_json_form_roundtrips(self, reset_mcp_settings):
        raw = build_laya_mcp_config_json(None)
        parsed = json.loads(raw)
        assert parsed["mcpServers"]["laya"]["type"] == "sse"

    def test_allowed_tool_flags_track_user_scopes(self, reset_mcp_settings):
        s = load_settings()
        s["mcp"] = {
            "tool_scopes": {"read": True, "write": False, "egress": False},
            "auth_mode": "none",
        }
        save_settings(s)
        flags = laya_allowed_tool_flags()
        names_in_flags = {f for f in flags if f.startswith("mcp__laya__")}
        assert "mcp__laya__search_cards" in names_in_flags
        assert "mcp__laya__dismiss_card" not in names_in_flags  # write disabled

        s["mcp"]["tool_scopes"]["write"] = True
        save_settings(s)
        flags = laya_allowed_tool_flags()
        names_in_flags = {f for f in flags if f.startswith("mcp__laya__")}
        assert "mcp__laya__dismiss_card" in names_in_flags

    def test_allowed_tool_flags_empty_when_scopes_off(self, reset_mcp_settings):
        s = load_settings()
        s["mcp"] = {
            "tool_scopes": {"read": False, "write": False, "egress": False},
            "auth_mode": "none",
        }
        save_settings(s)
        assert laya_allowed_tool_flags() == []


class TestClaudeCodeMcpArgs:
    """Verify `claude -p` is spawned with the new HTTP/SSE config + Laya allowlist."""

    @pytest.mark.asyncio
    async def test_start_session_passes_mcp_config(self, reset_mcp_settings):
        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="Find cab payment cards",
                repo_path="/tmp/repo",
                space_id="space_abc",
            )
        args = spawn.call_args.kwargs["args"]
        assert "--mcp-config" in args
        # The token-bearing config is written to a 0600 temp file (not passed
        # inline in argv, which would leak it to `ps`) — review §6.
        cfg_path = args[args.index("--mcp-config") + 1]
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert cfg["mcpServers"]["laya"]["type"] == "sse"
        assert cfg["mcpServers"]["laya"]["url"].endswith("?space_id=space_abc")
        assert (os.stat(cfg_path).st_mode & 0o777) == 0o600

    @pytest.mark.asyncio
    async def test_start_session_includes_user_scope_allowlist(self, reset_mcp_settings):
        s = load_settings()
        s["mcp"] = {
            "tool_scopes": {"read": True, "write": False, "egress": False},
            "auth_mode": "none",
        }
        save_settings(s)

        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="test",
                repo_path="/tmp/repo",
            )
        args = spawn.call_args.kwargs["args"]
        assert "mcp__laya__search_cards" in args
        assert "mcp__laya__dismiss_card" not in args

    @pytest.mark.asyncio
    async def test_start_session_prepends_mcp_hint_to_prompt(self, reset_mcp_settings):
        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="original user prompt",
                repo_path="/tmp/repo",
            )
        args = spawn.call_args.kwargs["args"]
        effective_prompt = args[args.index("-p") + 1]
        assert MCP_PROMPT_HINT in effective_prompt
        assert "original user prompt" in effective_prompt

    @pytest.mark.asyncio
    async def test_resume_passes_mcp_config(self, reset_mcp_settings):
        agent = ClaudeCodeAgent()
        agent._cc_session_id = "cc-sess-uuid"
        agent._repo_path = "/tmp/repo"
        with patch("laya.agents.claude_code.AgentProcess") as process_cls:
            proc = process_cls.return_value
            proc.spawn = AsyncMock()
            await agent.resume_with_answer(
                answer_text="continue",
                space_id="space_xyz",
            )
            args = proc.spawn.call_args.kwargs["args"]
        assert "--mcp-config" in args
        cfg_path = args[args.index("--mcp-config") + 1]
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert cfg["mcpServers"]["laya"]["url"].endswith("?space_id=space_xyz")
