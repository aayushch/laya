# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cursor Agent CLI adapter.

Event shapes are verbatim from `agent -p --output-format stream-json`
(version 2026.09.23) so the parser is exercised against real output.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from laya.agents.cursor_cli import CursorCliAgent, _mode_args
from laya.models.workspace import SessionStatus, WorkspaceEventActor, WorkspaceEventType

SID = "d64e7a67-3499-4f72-b14c-40a4f0521186"


@pytest.fixture
def agent():
    a = CursorCliAgent(binary_path="/x/agent")
    a._session_id = "sess_test"
    return a


def _tool_call(kind: str, subtype: str, args: dict, result: dict | None = None, call_id: str = "tc-1"):
    body: dict = {"args": args, "toolCallId": call_id, "startedAtMs": "1"}
    if result is not None:
        body["result"] = result
    return json.dumps({
        "type": "tool_call", "subtype": subtype, "call_id": f"{call_id}\nfc_x",
        "tool_call": {f"{kind}ToolCall": body, "hookAdditionalContexts": [], "toolCallId": call_id},
        "session_id": SID,
    })


class TestParseStreamJson:
    def test_init_captures_session_id(self, agent):
        line = json.dumps({
            "type": "system", "subtype": "init", "apiKeySource": "login", "cwd": "/repo",
            "session_id": SID, "model": "Auto", "permissionMode": "default",
        })
        events = agent._parse_stream_json(line)
        assert agent.cc_session_id == SID
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.STATUS_CHANGE
        assert events[0].content == {
            "status": "init", "session_id": SID, "model": "Auto", "cwd": "/repo",
            "permission_mode": "default",
        }

    def test_assistant_text_with_model_call_id(self, agent):
        line = json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "I'll read it."}]},
            "session_id": SID, "model_call_id": "mc-1", "timestamp_ms": 1,
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        assert events[0].event_type == WorkspaceEventType.AGENT_MESSAGE
        assert events[0].actor == WorkspaceEventActor.AGENT
        assert events[0].content == {"text": "I'll read it."}
        assert events[0].agent_message_id == "mc-1:0"
        assert agent._assistant_texts == ["I'll read it."]

    def test_assistant_text_without_model_call_id(self, agent):
        line = json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
            "session_id": SID,
        })
        events = agent._parse_stream_json(line)
        assert events[0].agent_message_id is None

    def test_assistant_multiple_blocks_and_empty_skipped(self, agent):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": ""},
                                    {"type": "text", "text": "b"}]},
        })
        events = agent._parse_stream_json(line)
        assert [e.content["text"] for e in events] == ["a", "b"]
        assert agent._assistant_texts == ["a", "b"]

    @pytest.mark.parametrize("line", [
        json.dumps({"type": "thinking", "subtype": "delta", "text": "hmm", "session_id": SID}),
        json.dumps({"type": "thinking", "subtype": "completed", "session_id": SID}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "p"}]}}),
        json.dumps({"type": "interaction_query", "subtype": "request", "query_type": "createPlanRequestQuery"}),
        json.dumps({"type": "interaction_query", "subtype": "response", "query_type": "createPlanRequestQuery"}),
        json.dumps({"type": "system", "subtype": "other"}),
        json.dumps({"type": "something_new"}),
        json.dumps([1, 2]),
    ])
    def test_noise_events_yield_nothing(self, agent, line):
        assert agent._parse_stream_json(line) == []

    def test_non_json_returns_none(self, agent):
        assert agent._parse_stream_json("Shell cwd was reset") is None

    def test_read_tool_call(self, agent):
        events = agent._parse_stream_json(_tool_call("read", "started", {"path": "/repo/hello.txt"}))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == WorkspaceEventType.FILE_READ
        assert e.content["tool"] == "Read"
        assert e.content["cursor_tool"] == "read"
        assert e.content["file"] == "/repo/hello.txt"
        assert e.content["tool_call_id"] == "tc-1"
        assert e.content["input"] == {"path": "/repo/hello.txt"}

    def test_edit_tool_call_strips_stream_content(self, agent):
        events = agent._parse_stream_json(
            _tool_call("edit", "started", {"path": "/repo/out.txt", "streamContent": "hello\n"})
        )
        e = events[0]
        assert e.event_type == WorkspaceEventType.FILE_WRITE
        assert e.content["tool"] == "Edit"
        assert e.content["file"] == "/repo/out.txt"
        assert "streamContent" not in e.content["input"]
        assert e.content["input"]["stream_content_len"] == 6

    def test_shell_tool_call_maps_to_bash(self, agent):
        events = agent._parse_stream_json(
            _tool_call("shell", "started", {"command": "echo SHELLRAN", "workingDirectory": ""})
        )
        e = events[0]
        assert e.event_type == WorkspaceEventType.TOOL_CALL
        assert e.content["tool"] == "Bash"
        assert e.content["input"]["command"] == "echo SHELLRAN"
        assert "file" not in e.content

    def test_shell_rejection_noted_once(self, agent):
        rejected = {"rejected": {"command": "echo SHELLRAN", "workingDirectory": "/repo", "reason": ""}}
        first = agent._parse_stream_json(_tool_call("shell", "completed", {}, rejected, call_id="tc-1"))
        assert len(first) == 1
        assert first[0].event_type == WorkspaceEventType.AGENT_MESSAGE
        assert first[0].actor == WorkspaceEventActor.SYSTEM
        assert "echo SHELLRAN" in first[0].content["text"]
        assert first[0].content["note"] == "shell_rejected"
        second = agent._parse_stream_json(_tool_call("shell", "completed", {}, rejected, call_id="tc-2"))
        assert second == []

    def test_completed_success_is_silent(self, agent):
        events = agent._parse_stream_json(
            _tool_call("shell", "completed", {"command": "ls"}, {"success": {"stdout": "x"}})
        )
        assert events == []
        events = agent._parse_stream_json(
            _tool_call("read", "completed", {"path": "/f"}, {"success": {"content": "c"}})
        )
        assert events == []

    def test_create_plan_emitted_once(self, agent):
        args = {"plan": "# Plan\n\nDo the thing.", "todos": [{"id": "t1", "content": "x"}]}
        started = agent._parse_stream_json(_tool_call("createPlan", "started", args, call_id="p1"))
        assert len(started) == 1
        assert started[0].event_type == WorkspaceEventType.AGENT_MESSAGE
        assert started[0].content["is_plan"] is True
        assert started[0].content["text"] == "# Plan\n\nDo the thing."
        assert started[0].content["todos"] == args["todos"]
        completed = agent._parse_stream_json(
            _tool_call("createPlan", "completed", args, {"success": {}}, call_id="p1")
        )
        assert completed == []

    def test_create_plan_falls_back_to_completed(self, agent):
        """If a build only populates args at completion, the plan still surfaces."""
        started = agent._parse_stream_json(_tool_call("createPlan", "started", {}, call_id="p2"))
        assert started == []
        completed = agent._parse_stream_json(
            _tool_call("createPlan", "completed", {"plan": "later"}, {"success": {}}, call_id="p2")
        )
        assert len(completed) == 1 and completed[0].content["is_plan"] is True

    def test_unknown_tool_kind_is_title_cased(self, agent):
        events = agent._parse_stream_json(_tool_call("fooBar", "started", {"q": 1}))
        assert events[0].event_type == WorkspaceEventType.TOOL_CALL
        assert events[0].content["tool"] == "FooBar"
        assert events[0].content["cursor_tool"] == "fooBar"

    def test_tool_call_without_kind_key_ignored(self, agent):
        line = json.dumps({"type": "tool_call", "subtype": "started", "tool_call": {"toolCallId": "x"}})
        assert agent._parse_stream_json(line) == []

    def test_result_joins_assistant_chunks(self, agent):
        for text in ("I'll read hello.txt.", "The secret word is PINEAPPLE."):
            agent._parse_stream_json(json.dumps({
                "type": "assistant", "message": {"content": [{"type": "text", "text": text}]},
            }))
        line = json.dumps({
            "type": "result", "subtype": "success", "duration_ms": 7826, "duration_api_ms": 7826,
            "is_error": False, "result": "I'll read hello.txt.The secret word is PINEAPPLE.",
            "session_id": SID, "request_id": "r",
            "usage": {"inputTokens": 7913, "outputTokens": 123, "cacheReadTokens": 15360, "cacheWriteTokens": 0},
        })
        events = agent._parse_stream_json(line)
        assert len(events) == 1
        c = events[0].content
        assert events[0].event_type == WorkspaceEventType.STATUS_CHANGE
        assert c["status"] == "result_received"
        assert c["result"] == "I'll read hello.txt.\n\nThe secret word is PINEAPPLE."
        assert c["cursor_result"] == "I'll read hello.txt.The secret word is PINEAPPLE."
        assert c["usage"]["inputTokens"] == 7913
        assert c["duration_ms"] == 7826
        assert c["is_error"] is False

    def test_result_error_also_emits_error_event(self, agent):
        line = json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": "boom"})
        events = agent._parse_stream_json(line)
        assert [e.event_type for e in events] == [WorkspaceEventType.STATUS_CHANGE, WorkspaceEventType.ERROR]
        assert events[1].content["error"] == "boom"


class TestModeArgs:
    @pytest.mark.parametrize("mode,expected", [
        ("plan", ["--mode=plan"]),
        ("ask", ["--mode=ask"]),
        ("acceptEdits", []),
        ("agent", []),
        ("edit", []),
        ("default", []),
        ("force", ["--force"]),
        ("full-auto", ["--force"]),
        ("yolo", ["--force"]),
        ("bogus", []),
        (None, []),
    ])
    def test_mode_table(self, mode, expected):
        assert _mode_args(mode, research=False) == expected

    def test_research_overrides_read_only_modes(self):
        assert _mode_args("plan", research=True) == []
        assert _mode_args("ask", research=True) == []

    def test_research_keeps_force(self):
        assert _mode_args("force", research=True) == ["--force"]


class TestBuildArgs:
    def test_base_flags_and_prompt_last(self, agent):
        args = agent._build_args("do it", mode="plan", research=False, add_dirs=None)
        assert args[:5] == ["/x/agent", "-p", "--trust", "--output-format", "stream-json"]
        assert "--mode=plan" in args
        assert args[-1] == "do it"
        assert "--model" not in args
        assert "--workspace" not in args

    def test_add_dirs_and_resume(self, agent):
        args = agent._build_args(
            "next", mode="acceptEdits", research=False, add_dirs=["/a", "/b"], resume_id="abc",
        )
        assert "--resume=abc" in args
        assert args[args.index("--add-dir") + 1] == "/a"
        assert args.count("--add-dir") == 2
        assert "--mode=plan" not in args and "--force" not in args
        assert args[-1] == "next"

    def test_force_mode(self, agent):
        args = agent._build_args("x", mode="force", research=False, add_dirs=None)
        assert "--force" in args


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_session_spawns_in_repo(self, agent):
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session("sess_1", "Fix it", "/repo", add_dirs=["/extra"])
        assert agent.get_status() == SessionStatus.RUNNING
        assert spawn.call_args.kwargs["cwd"] == "/repo"
        args = spawn.call_args.kwargs["args"]
        assert "--mode=plan" in args  # default mode when none given
        assert args[args.index("--add-dir") + 1] == "/extra"
        assert args[-1] == "Fix it"

    async def test_start_session_research_uses_default_mode(self, agent):
        with patch.object(agent._process, "spawn", new=AsyncMock()) as spawn:
            await agent.start_session("sess_1", "Research", "/tmp/research/c", mode="plan", research=True)
        args = spawn.call_args.kwargs["args"]
        assert "--mode=plan" not in args and "--force" not in args

    async def test_resume_without_session_id_raises(self, agent):
        with pytest.raises(ValueError):
            await agent.resume_with_answer("more")

    async def test_resume_passes_resume_flag(self, agent):
        agent._cursor_session_id = SID
        agent._repo_path = "/repo"
        with patch("laya.agents.cursor_cli.AgentProcess") as proc_cls:
            proc_cls.return_value.spawn = AsyncMock()
            await agent.resume_with_answer("Implement the plan", mode="acceptEdits")
            spawn = proc_cls.return_value.spawn
        assert spawn.call_args.kwargs["cwd"] == "/repo"
        args = spawn.call_args.kwargs["args"]
        assert f"--resume={SID}" in args
        assert "--mode=plan" not in args
        assert args[-1] == "Implement the plan"

    async def test_resume_defaults_to_accept_edits(self, agent):
        agent._cursor_session_id = SID
        with patch("laya.agents.cursor_cli.AgentProcess") as proc_cls:
            proc_cls.return_value.spawn = AsyncMock()
            await agent.resume_with_answer("go")
            args = proc_cls.return_value.spawn.call_args.kwargs["args"]
        assert "--mode=plan" not in args and "--force" not in args


def test_approval_prompt_never_detected(agent):
    assert agent._is_approval_prompt("Do you want to proceed? [Y/n]") is False


def _lines(*items):
    async def gen(idle_timeout: float = 300.0):
        for it in items:
            yield it
    return gen


@pytest.mark.asyncio
class TestStreamEvents:
    async def test_full_stream_completes(self, agent):
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": SID, "model": "Auto", "cwd": "/r"}),
            "Some non-json notice",
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}]}}),
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Hi"}),
        ]
        agent._process.read_lines = _lines(*lines)
        agent._process.wait = AsyncMock(return_value=0)
        events = [e async for e in agent.stream_events()]
        assert events[0].content == {"status": "running", "agent": "cursor_cli"}
        assert events[1].content["status"] == "init"
        assert events[2].content == {"text": "Some non-json notice", "raw": True}
        assert events[2].actor == WorkspaceEventActor.SYSTEM
        assert events[3].content == {"text": "Hi"}
        assert events[4].content["status"] == "result_received"
        assert events[-1].content["status"] == "completed"
        assert agent.get_status() == SessionStatus.COMPLETED
        assert agent.cc_session_id == SID

    async def test_sigterm_exit_is_cancelled(self, agent):
        agent._process.read_lines = _lines()
        agent._process.wait = AsyncMock(return_value=143)
        events = [e async for e in agent.stream_events()]
        assert events[-1].content["status"] == "cancelled"
        assert agent.get_status() == SessionStatus.CANCELLED

    async def test_nonzero_exit_reports_stderr(self, agent):
        agent._process.read_lines = _lines()
        agent._process.wait = AsyncMock(return_value=1)
        agent._process._stderr_lines.append("ActionRequiredError: Named models unavailable")
        events = [e async for e in agent.stream_events()]
        assert events[-1].event_type == WorkspaceEventType.ERROR
        assert "Named models unavailable" in events[-1].content["error"]
        assert agent.get_status() == SessionStatus.FAILED
