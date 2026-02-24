"""Claude Code CLI adapter for the CodingAgent protocol."""

from __future__ import annotations

import json
import re
import uuid
from typing import AsyncIterator

import structlog

from laya.agents.base import CodingAgent
from laya.agents.subprocess_helper import AgentProcess, strip_ansi
from laya.models.workspace import (
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
)

log = structlog.get_logger()

# Regex patterns for detecting approval prompts in agent output
APPROVAL_PATTERNS = [
    re.compile(r"Do you want to (proceed|continue|allow|approve)\?", re.IGNORECASE),
    re.compile(
        r"(Allow|Approve|Accept|Permit) (this|the) (change|edit|modification|write)\?",
        re.IGNORECASE,
    ),
    re.compile(r"(Modify|Edit|Write|Create|Delete) \d+ files?\?", re.IGNORECASE),
    re.compile(r"\[Y/n\]", re.IGNORECASE),
    re.compile(r"\(y/N\)", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
]


class ClaudeCodeAgent(CodingAgent):
    """Claude Code CLI adapter.

    Spawns `claude -p "<prompt>" --output-format stream-json` as a subprocess.
    Parses the JSON stream lines for structured events.
    """

    def __init__(self) -> None:
        self._process = AgentProcess()
        self._session_id: str = ""
        self._status: SessionStatus = SessionStatus.STARTING

    async def start_session(self, session_id: str, prompt: str, repo_path: str) -> None:
        self._session_id = session_id
        self._status = SessionStatus.STARTING

        args = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Parse Claude Code's stream-json output into WorkspaceEvents."""
        yield self._make_event(
            WorkspaceEventType.STATUS_CHANGE,
            WorkspaceEventActor.SYSTEM,
            {"status": "running", "agent": "claude_code"},
        )

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            # Try to parse as JSON (stream-json format)
            event = self._parse_stream_json(line)
            if event:
                yield event
                continue

            # Fallback: raw text output
            if self._is_approval_prompt(line):
                self._status = SessionStatus.AWAITING_INPUT
                yield self._make_event(
                    WorkspaceEventType.APPROVAL_REQUEST,
                    WorkspaceEventActor.AGENT,
                    {"message": line},
                    requires_input=True,
                )
            else:
                yield self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": line},
                )

        # Agent finished
        exit_code = await self._process.wait()
        if exit_code == 0:
            self._status = SessionStatus.COMPLETED
            yield self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "completed", "exit_code": exit_code},
            )
        else:
            self._status = SessionStatus.FAILED
            yield self._make_event(
                WorkspaceEventType.ERROR,
                WorkspaceEventActor.SYSTEM,
                {"error": f"Agent exited with code {exit_code}", "exit_code": exit_code},
            )

    def _parse_stream_json(self, line: str) -> WorkspaceEvent | None:
        """Parse a stream-json line from Claude Code."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")

        if event_type == "assistant":
            content_text = data.get("message", {}).get("content", "")
            if isinstance(content_text, list):
                texts = [b.get("text", "") for b in content_text if b.get("type") == "text"]
                content_text = "\n".join(texts)
            return self._make_event(
                WorkspaceEventType.AGENT_MESSAGE,
                WorkspaceEventActor.AGENT,
                {"text": content_text},
            )

        elif event_type == "tool_use":
            tool_name = data.get("name", "unknown")
            tool_input = data.get("input", {})

            if tool_name in ("Read", "ReadFile", "read_file"):
                return self._make_event(
                    WorkspaceEventType.FILE_READ,
                    WorkspaceEventActor.AGENT,
                    {"file": tool_input.get("file_path", ""), "tool": tool_name},
                )
            elif tool_name in ("Write", "WriteFile", "Edit", "edit_file"):
                return self._make_event(
                    WorkspaceEventType.FILE_WRITE,
                    WorkspaceEventActor.AGENT,
                    {"file": tool_input.get("file_path", ""), "tool": tool_name},
                )
            else:
                return self._make_event(
                    WorkspaceEventType.TOOL_CALL,
                    WorkspaceEventActor.AGENT,
                    {"tool": tool_name, "input": tool_input},
                )

        elif event_type == "result":
            return self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "result_received", "result": data.get("result", "")},
            )

        return None

    def _is_approval_prompt(self, text: str) -> bool:
        """Check if a line of text is an approval prompt."""
        return any(pattern.search(text) for pattern in APPROVAL_PATTERNS)

    def _make_event(
        self,
        event_type: WorkspaceEventType,
        actor: WorkspaceEventActor,
        content: dict,
        requires_input: bool = False,
    ) -> WorkspaceEvent:
        return WorkspaceEvent(
            event_id=f"we_{uuid.uuid4().hex[:12]}",
            session_id=self._session_id,
            event_type=event_type,
            actor=actor,
            content=content,
            requires_input=requires_input,
        )

    async def send_input(self, text: str) -> None:
        if self._status == SessionStatus.AWAITING_INPUT:
            self._status = SessionStatus.RUNNING
        await self._process.write(text)

    async def pause(self) -> None:
        await self._process.pause()
        self._status = SessionStatus.PAUSED

    async def resume(self) -> None:
        await self._process.resume()
        self._status = SessionStatus.RUNNING

    async def cancel(self) -> None:
        await self._process.terminate()
        self._status = SessionStatus.CANCELLED

    def get_status(self) -> SessionStatus:
        return self._status
