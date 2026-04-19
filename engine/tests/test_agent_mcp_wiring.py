"""Tests for the MCP callback wiring in spawned CLI agents."""

import json
import sys
from unittest.mock import AsyncMock, patch

import pytest

from laya.agents.claude_code import ClaudeCodeAgent
from laya.agents.mcp_config import (
    LAYA_MCP_READ_TOOLS,
    MCP_PROMPT_HINT,
    build_laya_mcp_config,
    build_laya_mcp_config_json,
    laya_allowed_tool_flags,
)


class TestMcpConfigBuilder:
    def test_config_uses_engine_python(self):
        """Child MCP server must launch under the engine's venv interpreter."""
        cfg = build_laya_mcp_config(space_id="default")
        laya = cfg["mcpServers"]["laya"]
        assert laya["command"] == sys.executable
        assert laya["args"] == ["-m", "laya.mcp"]

    def test_env_contains_skip_migrations(self):
        cfg = build_laya_mcp_config(space_id="default")
        env = cfg["mcpServers"]["laya"]["env"]
        assert env["LAYA_MCP_SKIP_MIGRATIONS"] == "1"

    def test_env_contains_space_id_when_set(self):
        cfg = build_laya_mcp_config(space_id="space_abc")
        env = cfg["mcpServers"]["laya"]["env"]
        assert env["LAYA_SPACE_ID"] == "space_abc"

    def test_env_omits_space_id_when_none(self):
        cfg = build_laya_mcp_config(space_id=None)
        env = cfg["mcpServers"]["laya"]["env"]
        assert "LAYA_SPACE_ID" not in env

    def test_json_form_roundtrips(self):
        raw = build_laya_mcp_config_json("default")
        parsed = json.loads(raw)
        assert parsed["mcpServers"]["laya"]["args"] == ["-m", "laya.mcp"]

    def test_allowed_tool_flags_cover_all_read_tools(self):
        flags = laya_allowed_tool_flags()
        # Pairs of [--allowedTools, <tool-pattern>] => 2 entries per tool
        assert len(flags) == 2 * len(LAYA_MCP_READ_TOOLS)
        for tool in LAYA_MCP_READ_TOOLS:
            assert f"mcp__laya__{tool}" in flags


class TestClaudeCodeMcpArgs:
    """Verify claude -p is spawned with MCP config + Laya allowlist."""

    @pytest.mark.asyncio
    async def test_start_session_passes_mcp_config(self):
        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="Find cab payment cards",
                repo_path="/tmp/repo",
                space_id="space_abc",
            )
        args = spawn.call_args.kwargs["args"]
        # --mcp-config is present as flag+value pair
        assert "--mcp-config" in args
        cfg_json = args[args.index("--mcp-config") + 1]
        cfg = json.loads(cfg_json)
        assert cfg["mcpServers"]["laya"]["args"] == ["-m", "laya.mcp"]
        assert cfg["mcpServers"]["laya"]["env"]["LAYA_SPACE_ID"] == "space_abc"

    @pytest.mark.asyncio
    async def test_start_session_includes_laya_allowlist(self):
        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="test",
                repo_path="/tmp/repo",
            )
        args = spawn.call_args.kwargs["args"]
        # Every read tool must appear as an allowlist entry
        for tool in LAYA_MCP_READ_TOOLS:
            assert f"mcp__laya__{tool}" in args

    @pytest.mark.asyncio
    async def test_start_session_prepends_mcp_hint_to_prompt(self):
        agent = ClaudeCodeAgent()
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session(
                session_id="sess_test",
                prompt="original user prompt",
                repo_path="/tmp/repo",
            )
        args = spawn.call_args.kwargs["args"]
        # The prompt immediately follows the -p flag
        effective_prompt = args[args.index("-p") + 1]
        assert MCP_PROMPT_HINT in effective_prompt
        assert "original user prompt" in effective_prompt

    @pytest.mark.asyncio
    async def test_resume_passes_mcp_config(self):
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
        cfg_json = args[args.index("--mcp-config") + 1]
        cfg = json.loads(cfg_json)
        assert cfg["mcpServers"]["laya"]["env"]["LAYA_SPACE_ID"] == "space_xyz"
        # Resume preserves allowlist
        assert "mcp__laya__get_card" in args
