# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Cursor Agent CLI adapter for the CodingAgent protocol."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import structlog

from laya.agents.base import BaseCodingAgent
from laya.agents.subprocess_helper import AgentProcess, strip_ansi
from laya.models.workspace import (
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
)

log = structlog.get_logger()

# Laya permission_mode -> extra argv for `agent`. Cursor has three effective
# levels: `--mode=plan` / `--mode=ask` are read-only; the default (no flag)
# auto-applies file edits but rejects every shell command; `--force` also runs
# shell. "acceptEdits" is reused from Claude Code's vocabulary because it means
# the same thing here (edits land without asking) and keeps the workspace
# panel's Plan/Act toggle meaningful for Cursor sessions.
_MODE_FLAGS: dict[str, list[str]] = {
    "plan": ["--mode=plan"],
    "ask": ["--mode=ask"],
    "acceptEdits": [],
    "agent": [],
    "edit": [],
    "default": [],
    "force": ["--force"],
    "full-auto": ["--force"],
    "yolo": ["--force"],
}
_READ_ONLY_MODES = {"plan", "ask"}
_TOOL_CALL_SUFFIX = "ToolCall"

# Cursor tool kinds -> the tool names the workspace panel already knows how to
# render (command preview for Bash, pattern preview for Grep/Glob, file path for
# read/write events). The raw kind is kept alongside as `cursor_tool`.
_TOOL_NAMES: dict[str, str] = {
    "shell": "Bash",
    "grep": "Grep",
    "glob": "Glob",
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "delete": "Delete",
}
_FILE_READ_KINDS = {"read"}
_FILE_WRITE_KINDS = {"edit", "write", "delete", "createFile", "multiEdit"}


def _mode_args(mode: str | None, research: bool) -> list[str]:
    """Translate a Laya permission mode into Cursor flags.

    Research sessions run in a Laya-owned directory and must write the research
    document there, but every caller (Run Agent modal, entity runs, processing
    rules) passes ``mode="plan"`` for them. Plan/ask are read-only in Cursor, so
    for research they collapse to the default mode (edits allowed, shell
    rejected). ``force`` is still honoured. Unknown modes fall back to default.
    """
    resolved = mode if mode in _MODE_FLAGS else "default"
    if research and resolved in _READ_ONLY_MODES:
        resolved = "default"
    return list(_MODE_FLAGS[resolved])


def _canonical_tool_name(kind: str) -> str:
    return _TOOL_NAMES.get(kind) or (kind[:1].upper() + kind[1:])


def _classify_tool(kind: str) -> WorkspaceEventType:
    if kind in _FILE_READ_KINDS:
        return WorkspaceEventType.FILE_READ
    if kind in _FILE_WRITE_KINDS:
        return WorkspaceEventType.FILE_WRITE
    return WorkspaceEventType.TOOL_CALL


class CursorCliAgent(BaseCodingAgent):
    """Cursor Agent CLI adapter.

    Spawns ``agent -p --trust --output-format stream-json "<prompt>"`` and parses
    the NDJSON event stream. Supports ``--add-dir``, session resumption via
    ``--resume=<session_id>``, and the plan / acceptEdits / force modes.
    """

    def __init__(self, binary_path: str = "agent") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._cursor_session_id: str | None = None
        self._repo_path: str = ""
        self._status: SessionStatus = SessionStatus.STARTING
        # Per-run stream state, reset at the top of stream_events().
        self._assistant_texts: list[str] = []
        self._plan_emitted: set[str] = set()
        self._shell_rejection_noted: bool = False

    @property
    def cc_session_id(self) -> str | None:
        """Cursor's chat session UUID, stored in the generic cc_session_id column."""
        return self._cursor_session_id

    def _build_args(
        self,
        prompt: str,
        *,
        mode: str | None,
        research: bool,
        add_dirs: list[str] | None,
        resume_id: str | None = None,
    ) -> list[str]:
        # Never pass --model: Cursor persists it as the user's default model in
        # ~/.cursor/cli-config.json, silently changing their interactive CLI.
        # No --workspace either; the cwd handed to spawn() is the workspace.
        # --trust skips the workspace-trust prompt that would hang a headless run.
        args = [
            self._binary,
            "-p",
            "--trust",
            "--output-format",
            "stream-json",
            *_mode_args(mode, research),
        ]
        if resume_id:
            args.append(f"--resume={resume_id}")
        for d in add_dirs or []:
            args.extend(["--add-dir", d])
        args.append(prompt)
        return args

    async def start_session(
        self, session_id: str, prompt: str, repo_path: str, add_dirs: list[str] | None = None,
        mode: str | None = None, research: bool = False, space_id: str | None = None,
    ) -> None:
        # space_id: MCP wiring is not implemented for Cursor. The CLI has no
        # --mcp-config flag; servers come only from .cursor/mcp.json in the
        # repo or ~/.cursor/mcp.json, and writing either is invasive to the
        # user's own configuration.
        _ = space_id
        self._session_id = session_id
        self._repo_path = repo_path
        self._status = SessionStatus.STARTING

        args = self._build_args(
            prompt, mode=mode or "plan", research=research, add_dirs=add_dirs,
        )
        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def resume_with_answer(
        self,
        answer_text: str,
        add_dirs: list[str] | None = None,
        research: bool = False,
        mode: str | None = None,
        space_id: str | None = None,
    ) -> None:
        """Resume the Cursor chat with new instructions via ``--resume``."""
        _ = space_id
        if not self._cursor_session_id:
            raise ValueError("No Cursor session ID available for resumption")

        self._process = AgentProcess()
        self._status = SessionStatus.STARTING

        effective_mode = mode or ("plan" if research else "acceptEdits")
        args = self._build_args(
            answer_text, mode=effective_mode, research=research, add_dirs=add_dirs,
            resume_id=self._cursor_session_id,
        )
        await self._process.spawn(args=args, cwd=self._repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Parse Cursor's stream-json output into WorkspaceEvents."""
        yield self._make_event(
            WorkspaceEventType.STATUS_CHANGE,
            WorkspaceEventActor.SYSTEM,
            {"status": "running", "agent": "cursor_cli"},
        )
        self._assistant_texts = []
        self._plan_emitted = set()
        self._shell_rejection_noted = False

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            events = self._parse_stream_json(line)
            if events is not None:
                for event in events:
                    yield event
                continue

            # Non-JSON stdout (auth notices, update hints): persist as a raw
            # system message so it is visible in the timeline.
            yield self._make_event(
                WorkspaceEventType.AGENT_MESSAGE,
                WorkspaceEventActor.SYSTEM,
                {"text": line, "raw": True},
            )

        exit_code = await self._process.wait()
        yield self._terminal_status_event(exit_code)

    def _is_approval_prompt(self, text: str) -> bool:
        # Headless Cursor never blocks on approval: a clarifying question is
        # emitted as a plain assistant message followed by a normal exit.
        return False

    def _parse_stream_json(self, line: str) -> list[WorkspaceEvent] | None:
        """Parse one stream-json line into workspace events.

        Returns ``None`` when the line is not JSON, and an empty list for
        well-formed lines that carry nothing worth persisting (thinking deltas,
        the prompt echo, auto-answered interaction queries).
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return []

        msg_type = data.get("type", "")

        if msg_type == "system":
            return self._parse_system(data)
        if msg_type == "assistant":
            return self._parse_assistant(data)
        if msg_type == "tool_call":
            return self._parse_tool_call(data)
        if msg_type == "result":
            return self._parse_result(data)
        # user (prompt echo), thinking, interaction_query, anything unknown
        return []

    def _parse_system(self, data: dict) -> list[WorkspaceEvent]:
        if data.get("subtype") != "init":
            return []
        sid = data.get("session_id")
        if sid:
            self._cursor_session_id = sid
            log.info("cursor_session_id_captured", cursor_session_id=sid)
        meta: dict[str, Any] = {"status": "init"}
        for src, dst in (
            ("session_id", "session_id"),
            ("model", "model"),
            ("cwd", "cwd"),
            ("permissionMode", "permission_mode"),
        ):
            if src in data:
                meta[dst] = data[src]
        return [self._make_event(WorkspaceEventType.STATUS_CHANGE, WorkspaceEventActor.SYSTEM, meta)]

    def _parse_assistant(self, data: dict) -> list[WorkspaceEvent]:
        blocks = (data.get("message") or {}).get("content", [])
        if not isinstance(blocks, list):
            return []
        call_id = data.get("model_call_id")
        events: list[WorkspaceEvent] = []
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not text:
                continue
            self._assistant_texts.append(text)
            events.append(
                self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": text},
                    agent_message_id=f"{call_id}:{idx}" if call_id else None,
                )
            )
        return events

    def _parse_tool_call(self, data: dict) -> list[WorkspaceEvent]:
        payload = data.get("tool_call") or {}
        kind_key = next(
            (k for k in payload if isinstance(k, str) and k.endswith(_TOOL_CALL_SUFFIX)),
            None,
        )
        if not kind_key:
            return []
        kind = kind_key[: -len(_TOOL_CALL_SUFFIX)]
        body = payload.get(kind_key) or {}
        args = body.get("args") or {}
        # Cursor's ids join two parts with a literal newline. Kept verbatim: the
        # value is only a correlation / dedup key, never displayed.
        tool_call_id = payload.get("toolCallId") or data.get("call_id") or ""
        subtype = data.get("subtype")

        if kind == "createPlan":
            # Plan mode's deliverable. Emit once per call so the card's
            # staged_output picks it up as agent_plan and the panel renders it.
            if tool_call_id in self._plan_emitted:
                return []
            plan = args.get("plan", "")
            if not plan:
                return []
            self._plan_emitted.add(tool_call_id)
            return [
                self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": plan, "is_plan": True, "todos": args.get("todos", [])},
                )
            ]

        if subtype == "started":
            evt_type = _classify_tool(kind)
            stored_args = dict(args)
            # Edits stream the whole new file body; keep only its size so a
            # large write doesn't bloat workspace_events.
            if "streamContent" in stored_args:
                stored_args["stream_content_len"] = len(stored_args.pop("streamContent") or "")
            content: dict[str, Any] = {
                "tool": _canonical_tool_name(kind),
                "cursor_tool": kind,
                "input": stored_args,
                "tool_call_id": tool_call_id,
            }
            if evt_type in (WorkspaceEventType.FILE_READ, WorkspaceEventType.FILE_WRITE):
                content["file"] = args.get("path") or args.get("file_path") or ""
            return [self._make_event(evt_type, WorkspaceEventActor.AGENT, content)]

        if subtype == "completed" and kind == "shell":
            rejected = (body.get("result") or {}).get("rejected")
            if rejected and not self._shell_rejection_noted:
                # Default mode silently drops shell commands; without this note
                # the timeline shows a command that "ran" with no effect.
                self._shell_rejection_noted = True
                cmd = rejected.get("command") or args.get("command") or ""
                return [
                    self._make_event(
                        WorkspaceEventType.AGENT_MESSAGE,
                        WorkspaceEventActor.SYSTEM,
                        {
                            "text": (
                                f"Cursor rejected a shell command (`{cmd}`). "
                                "Shell commands only run in Full Access mode."
                            ),
                            "note": "shell_rejected",
                        },
                    )
                ]
        return []

    def _parse_result(self, data: dict) -> list[WorkspaceEvent]:
        # Cursor's own `result` string concatenates every assistant chunk with
        # no separator, so the staged result is rebuilt from the chunks.
        content: dict[str, Any] = {
            "status": "result_received",
            "result": "\n\n".join(self._assistant_texts),
            "cursor_result": data.get("result", ""),
            "usage": data.get("usage") or {},
            "duration_ms": data.get("duration_ms"),
            "is_error": bool(data.get("is_error")),
        }
        events = [self._make_event(WorkspaceEventType.STATUS_CHANGE, WorkspaceEventActor.SYSTEM, content)]
        if content["is_error"]:
            events.append(
                self._make_event(
                    WorkspaceEventType.ERROR,
                    WorkspaceEventActor.SYSTEM,
                    {"error": data.get("result") or "Cursor reported an error"},
                )
            )
        return events
