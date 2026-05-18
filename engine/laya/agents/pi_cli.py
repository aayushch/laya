"""Pi CLI adapter for the CodingAgent protocol."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator

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

APPROVAL_PATTERNS = [
    re.compile(r"Do you want to (proceed|continue)\?", re.IGNORECASE),
    re.compile(r"\[Y/n\]", re.IGNORECASE),
    re.compile(r"\(y/N\)", re.IGNORECASE),
]


class PiCliAgent(CodingAgent):
    """Pi CLI adapter.

    Spawns ``pi --mode json "prompt"`` as a subprocess and parses
    the v3 JSON event stream.  Supports session resumption via
    ``pi --mode json --session <session_id> "prompt"``.

    Pi has no built-in sandbox/permission modes or MCP support.
    It supports 15+ providers including Ollama for local models.
    """

    def __init__(self, binary_path: str = "pi") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._pi_session_id: str | None = None
        self._repo_path: str = ""
        self._status: SessionStatus = SessionStatus.STARTING
        self._message_buffer: str = ""
        self._message_text_emitted: bool = False

    @property
    def cc_session_id(self) -> str | None:
        """Pi's session UUID, stored in the generic cc_session_id column."""
        return self._pi_session_id

    async def start_session(
        self, session_id: str, prompt: str, repo_path: str, add_dirs: list[str] | None = None,
        mode: str | None = None, research: bool = False, space_id: str | None = None,
    ) -> None:
        # Pi has no MCP support — space_id is unused.
        _ = (mode, space_id)
        self._session_id = session_id
        self._repo_path = repo_path
        self._status = SessionStatus.STARTING

        args = [self._binary, "--mode", "json"]

        if add_dirs:
            dirs_hint = (
                "You also have access to files in these additional directories: "
                + ", ".join(add_dirs)
            )
            args.extend(["--append-system-prompt", dirs_hint])

        args.append(prompt)

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
        _ = (mode, space_id)
        if not self._pi_session_id:
            raise ValueError("No Pi session ID available for resumption")

        self._process = AgentProcess()
        self._status = SessionStatus.STARTING

        args = [
            self._binary,
            "--mode", "json",
            "--session", self._pi_session_id,
        ]

        if add_dirs:
            dirs_hint = (
                "You also have access to files in these additional directories: "
                + ", ".join(add_dirs)
            )
            args.extend(["--append-system-prompt", dirs_hint])

        args.append(answer_text)

        await self._process.spawn(args=args, cwd=self._repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Parse Pi's JSON event stream into WorkspaceEvents."""
        yield self._make_event(
            WorkspaceEventType.STATUS_CHANGE,
            WorkspaceEventActor.SYSTEM,
            {"status": "running", "agent": "pi_cli"},
        )

        self._message_buffer = ""

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            events = self._parse_json_event(line)
            if events is not None:
                for event in events:
                    yield event
                continue

            # Non-JSON line: flush any pending message buffer first
            flushed = self._flush_message_buffer()
            if flushed:
                yield flushed

            if any(p.search(line) for p in APPROVAL_PATTERNS):
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
                    WorkspaceEventActor.SYSTEM,
                    {"text": line, "raw": True},
                )

        # Flush any remaining message buffer at end of stream
        flushed = self._flush_message_buffer()
        if flushed:
            yield flushed

        exit_code = await self._process.wait()

        if self._status == SessionStatus.CANCELLED:
            yield self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "cancelled", "exit_code": exit_code},
            )
        elif exit_code == 0:
            self._status = SessionStatus.COMPLETED
            yield self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "completed", "exit_code": exit_code},
            )
        elif exit_code in (143, -15):
            self._status = SessionStatus.CANCELLED
            yield self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "cancelled", "exit_code": exit_code},
            )
        else:
            self._status = SessionStatus.FAILED
            stderr = self._process.stderr_output
            error_msg = f"Agent exited with code {exit_code}"
            if stderr:
                error_msg += f": {stderr}"
            yield self._make_event(
                WorkspaceEventType.ERROR,
                WorkspaceEventActor.SYSTEM,
                {"error": error_msg, "exit_code": exit_code},
            )

    # ------------------------------------------------------------------
    # JSON event parsing (Pi v3 event schema)
    # ------------------------------------------------------------------

    def _parse_json_event(self, line: str) -> list[WorkspaceEvent] | None:
        """Parse a single JSONL line from Pi's --mode json output.

        Returns a list of WorkspaceEvents, an empty list if parsed but
        no events to emit, or None if the line is not valid JSON.

        Pi v3 event types:
        - session: header with session id, version, cwd
        - agent_start / agent_end: agent lifecycle
        - turn_start / turn_end: turn boundaries
        - message_start / message_update / message_end: message lifecycle
        - tool_execution_start / tool_execution_update / tool_execution_end
        - compaction_start / compaction_end: context compaction
        - auto_retry_start / auto_retry_end: automatic retries
        - queue_update: steering/follow-up queue changes
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")

        # --- session header: capture session id ---
        if event_type == "session":
            sid = data.get("id")
            if sid:
                self._pi_session_id = sid
                log.info("pi_session_id_captured", pi_session_id=sid)
            meta: dict[str, Any] = {"status": "init"}
            if "id" in data:
                meta["session_id"] = data["id"]
            if "cwd" in data:
                meta["cwd"] = data["cwd"]
            if "version" in data:
                meta["version"] = data["version"]
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    meta,
                )
            ]

        # --- agent lifecycle ---
        if event_type == "agent_start":
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "agent_start"},
                )
            ]

        if event_type == "agent_end":
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "agent_end"},
                )
            ]

        # --- turn boundaries ---
        if event_type == "turn_start":
            return []

        if event_type == "turn_end":
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "turn_completed"},
                )
            ]

        # --- message lifecycle ---
        if event_type == "message_start":
            msg = data.get("message", {})
            role = msg.get("role", "") if isinstance(msg, dict) else ""
            if role in ("user", "toolResult"):
                return []
            self._message_buffer = ""
            self._message_text_emitted = False
            return []

        if event_type == "message_update":
            return self._parse_message_update(data)

        if event_type == "message_end":
            msg = data.get("message", {})
            role = msg.get("role", "") if isinstance(msg, dict) else ""
            if role in ("user", "toolResult"):
                return []

            events: list[WorkspaceEvent] = []
            text = self._message_buffer
            self._message_buffer = ""

            # Only fall back to extracting from the final message object
            # if no text was already emitted during streaming (text_delta
            # flushed by toolcall_start or tool_execution_start).
            if not text and not self._message_text_emitted:
                text = self._extract_text_blocks(msg)

            if text and text.strip():
                events.append(
                    self._make_event(
                        WorkspaceEventType.AGENT_MESSAGE,
                        WorkspaceEventActor.AGENT,
                        {"text": text},
                    )
                )
            return events

        # --- tool execution ---
        if event_type == "tool_execution_start":
            tool_name = data.get("toolName", "unknown")
            tool_call_id = data.get("toolCallId", "")
            tool_args = data.get("args", {})

            # Flush message buffer before tool use — preceding text is the
            # agent's reasoning before invoking the tool
            events = []
            flushed = self._flush_message_buffer()
            if flushed:
                events.append(flushed)

            evt_type = self._classify_tool(tool_name)
            content: dict[str, Any] = {
                "tool": tool_name,
                "input": tool_args,
                "tool_call_id": tool_call_id,
            }
            if evt_type in (WorkspaceEventType.FILE_READ, WorkspaceEventType.FILE_WRITE):
                content["file"] = (
                    tool_args.get("file_path", "")
                    or tool_args.get("path", "")
                    or tool_args.get("file", "")
                )
            events.append(
                self._make_event(evt_type, WorkspaceEventActor.AGENT, content)
            )
            return events

        if event_type == "tool_execution_update":
            return []

        if event_type == "tool_execution_end":
            tool_name = data.get("toolName", "unknown")
            tool_call_id = data.get("toolCallId", "")
            raw_result = data.get("result", "")
            is_error = data.get("isError", False)

            # result can be a structured object {content: [{type, text}]}
            result_text = raw_result
            if isinstance(raw_result, dict):
                parts = []
                for block in raw_result.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                result_text = "".join(parts) if parts else str(raw_result)

            content = {
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "result": result_text,
                "is_error": is_error,
            }
            evt_type = WorkspaceEventType.TOOL_CALL
            return [
                self._make_event(
                    evt_type,
                    WorkspaceEventActor.SYSTEM,
                    content,
                )
            ]

        # --- compaction ---
        if event_type in ("compaction_start", "compaction_end"):
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": event_type, "reason": data.get("reason", "")},
                )
            ]

        # --- auto retry ---
        if event_type in ("auto_retry_start", "auto_retry_end"):
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {
                        "status": event_type,
                        "attempt": data.get("attempt"),
                        "max_attempts": data.get("maxAttempts"),
                    },
                )
            ]

        # --- queue_update ---
        if event_type == "queue_update":
            return []

        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_message_update(self, data: dict[str, Any]) -> list[WorkspaceEvent]:
        """Route a message_update by its assistantMessageEvent subtype.

        Pi streams content blocks inside message_update events. Each has
        an assistantMessageEvent with a type that tells us what kind of
        delta it is:

        - thinking_start/delta/end: internal reasoning — skip
        - text_start/delta/end: visible assistant text — accumulate
        - toolcall_start/delta/end: tool call being assembled — flush
          text on start, skip delta, skip end (tool_execution_start
          handles the actual invocation)
        """
        ame = data.get("assistantMessageEvent", {})
        if not isinstance(ame, dict):
            return []

        ame_type = ame.get("type", "")

        # --- thinking: internal reasoning, not shown ---
        if ame_type in ("thinking_start", "thinking_delta", "thinking_end"):
            return []

        # --- text: visible assistant output ---
        if ame_type == "text_delta":
            delta = ame.get("delta", "")
            if delta:
                self._message_buffer += delta
            return []

        if ame_type in ("text_start", "text_end"):
            return []

        # --- toolcall: flush preceding text, let tool_execution handle the rest ---
        if ame_type == "toolcall_start":
            # Flush text accumulated before the tool call. If it's only
            # whitespace (common between thinking_end and toolcall_start),
            # discard it rather than emitting a blank message.
            events: list[WorkspaceEvent] = []
            if self._message_buffer.strip():
                flushed = self._flush_message_buffer()
                if flushed:
                    events.append(flushed)
            else:
                self._message_buffer = ""
            return events

        if ame_type in ("toolcall_delta", "toolcall_end"):
            return []

        return []

    @staticmethod
    def _extract_text_blocks(msg: dict[str, Any]) -> str:
        """Extract only text-type content blocks from a Pi message object.

        Skips thinking and toolCall blocks — only returns visible text.
        """
        if not isinstance(msg, dict):
            return ""
        content = msg.get("content", [])
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts)
        return ""

    def _flush_message_buffer(self) -> WorkspaceEvent | None:
        """Flush accumulated message text as a single AGENT_MESSAGE event."""
        if not self._message_buffer:
            return None
        text = self._message_buffer
        self._message_buffer = ""
        self._message_text_emitted = True
        return self._make_event(
            WorkspaceEventType.AGENT_MESSAGE,
            WorkspaceEventActor.AGENT,
            {"text": text},
        )

    @staticmethod
    def _classify_tool(tool_name: str) -> WorkspaceEventType:
        """Map a Pi tool name to the appropriate WorkspaceEventType.

        Pi's built-in tools use lowercase names: read, bash, edit, write,
        grep, find, ls.
        """
        if tool_name in ("read", "Read", "ReadFile", "read_file"):
            return WorkspaceEventType.FILE_READ
        if tool_name in ("write", "Write", "edit", "Edit", "WriteFile", "edit_file"):
            return WorkspaceEventType.FILE_WRITE
        return WorkspaceEventType.TOOL_CALL

    def _make_event(
        self,
        event_type: WorkspaceEventType,
        actor: WorkspaceEventActor,
        content: dict,
        requires_input: bool = False,
        agent_message_id: str | None = None,
    ) -> WorkspaceEvent:
        return WorkspaceEvent(
            event_id=f"we_{uuid.uuid4().hex[:12]}",
            session_id=self._session_id,
            event_type=event_type,
            actor=actor,
            content=content,
            requires_input=requires_input,
            agent_message_id=agent_message_id,
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
