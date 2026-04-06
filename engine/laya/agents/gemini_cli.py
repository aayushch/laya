"""Gemini CLI adapter for the CodingAgent protocol."""

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


class GeminiCliAgent(CodingAgent):
    """Gemini CLI adapter.

    Spawns `gemini -p "<prompt>" --output-format stream-json` as a subprocess.
    Parses the JSON stream lines for structured events.  Non-JSON lines
    (credential messages, rate-limit retries, etc.) are wrapped as
    AGENT_MESSAGE events so nothing is lost.
    """

    def __init__(self, binary_path: str = "gemini") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._gemini_session_id: str | None = None
        self._repo_path: str = ""
        self._status: SessionStatus = SessionStatus.STARTING
        # Buffer for assembling delta message chunks
        self._delta_buffer: str = ""

    @property
    def cc_session_id(self) -> str | None:
        """Gemini's internal session UUID (stored in the generic cc_session_id column)."""
        return self._gemini_session_id

    async def start_session(
        self, session_id: str, prompt: str, repo_path: str, add_dirs: list[str] | None = None,
        mode: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._repo_path = repo_path
        self._status = SessionStatus.STARTING

        args = [
            self._binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
        ]

        if add_dirs:
            for d in add_dirs:
                args.extend(["--include-directories", d])

        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def resume_with_answer(self, answer_text: str, add_dirs: list[str] | None = None) -> None:
        """Resume the Gemini CLI conversation with the user's answer.

        Spawns a new subprocess using --resume <session_id> so Gemini
        loads the conversation history and continues from where it left off.

        Args:
            answer_text: The user's response text.
            add_dirs: Extra directory paths to pass via --include-directories flags.
        """
        if not self._gemini_session_id:
            raise ValueError("No Gemini session ID available for resumption")

        self._process = AgentProcess()
        self._status = SessionStatus.STARTING

        args = [
            self._binary,
            "-p",
            answer_text,
            "--resume",
            self._gemini_session_id,
            "--output-format",
            "stream-json",
        ]

        if add_dirs:
            for d in add_dirs:
                args.extend(["--include-directories", d])

        await self._process.spawn(args=args, cwd=self._repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Parse Gemini CLI's stream-json output into WorkspaceEvents."""
        yield self._make_event(
            WorkspaceEventType.STATUS_CHANGE,
            WorkspaceEventActor.SYSTEM,
            {"status": "running", "agent": "gemini_cli"},
        )

        self._delta_buffer = ""

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            # Try to parse as JSON (stream-json format)
            events = self._parse_stream_json(line)
            if events is not None:
                for event in events:
                    yield event
                continue

            # Non-JSON line: credential messages, rate-limit retries, etc.
            # Flush any pending delta buffer first
            flushed = self._flush_delta_buffer()
            if flushed:
                yield flushed

            # Check for approval prompts in plain-text lines
            if any(p.search(line) for p in APPROVAL_PATTERNS):
                self._status = SessionStatus.AWAITING_INPUT
                yield self._make_event(
                    WorkspaceEventType.APPROVAL_REQUEST,
                    WorkspaceEventActor.AGENT,
                    {"message": line},
                    requires_input=True,
                )
            else:
                # Wrap non-JSON output as a system message so it's persisted
                yield self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.SYSTEM,
                    {"text": line, "raw": True},
                )

        # Flush any remaining delta buffer at end of stream
        flushed = self._flush_delta_buffer()
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

    def _parse_stream_json(self, line: str) -> list[WorkspaceEvent] | None:
        """Parse a stream-json line from Gemini CLI into workspace events.

        Returns a list of events (possibly empty if the line was parsed
        successfully but produced no events, e.g. delta chunks being buffered),
        or ``None`` if the line is not valid JSON.

        Gemini stream-json types:
        - init: session metadata (session_id, model)
        - message (role=user): echoed user prompt
        - message (role=assistant, delta=true): streaming assistant chunks
        - message (role=assistant, no delta): complete assistant message
        - tool_use: tool invocation
        - tool_result: tool output
        - result: final session stats
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        msg_type = data.get("type", "")

        # --- init: capture session_id and model ---
        if msg_type == "init":
            sid = data.get("session_id")
            if sid:
                self._gemini_session_id = sid
                log.info("gemini_session_id_captured", gemini_session_id=sid)
            meta: dict[str, Any] = {"status": "init"}
            if "model" in data:
                meta["model"] = data["model"]
            if "session_id" in data:
                meta["session_id"] = data["session_id"]
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    meta,
                )
            ]

        # --- message ---
        if msg_type == "message":
            role = data.get("role", "")
            content = data.get("content", "")
            is_delta = data.get("delta", False)

            # User message echo — just record it
            if role == "user":
                return [
                    self._make_event(
                        WorkspaceEventType.AGENT_MESSAGE,
                        WorkspaceEventActor.SYSTEM,
                        {"text": content, "echoed_prompt": True},
                    )
                ]

            # Assistant message
            if role == "assistant":
                if is_delta:
                    # Accumulate delta chunks into buffer
                    self._delta_buffer += content
                    return []
                else:
                    # Complete message (non-delta) — emit directly
                    return [
                        self._make_event(
                            WorkspaceEventType.AGENT_MESSAGE,
                            WorkspaceEventActor.AGENT,
                            {"text": content},
                        )
                    ]

            return []

        # --- tool_use ---
        if msg_type == "tool_use":
            tool_name = data.get("tool_name", "unknown")
            tool_id = data.get("tool_id", "")
            params = data.get("parameters", {})

            evt_type = self._classify_tool(tool_name)
            tool_content: dict[str, Any] = {
                "tool": tool_name,
                "input": params,
                "tool_id": tool_id,
            }
            if evt_type in (WorkspaceEventType.FILE_READ, WorkspaceEventType.FILE_WRITE):
                tool_content["file"] = params.get("file_path", "")

            # Flush delta buffer before tool use — the preceding text is the
            # agent's reasoning before invoking the tool
            events: list[WorkspaceEvent] = []
            flushed = self._flush_delta_buffer()
            if flushed:
                events.append(flushed)
            events.append(self._make_event(evt_type, WorkspaceEventActor.AGENT, tool_content))
            return events

        # --- tool_result ---
        if msg_type == "tool_result":
            tool_id = data.get("tool_id", "")
            status = data.get("status", "")
            output = data.get("output", "")
            error = data.get("error")
            result_content: dict[str, Any] = {
                "tool_id": tool_id,
                "status": status,
                "output": output,
            }
            if error:
                result_content["error"] = error
            return [
                self._make_event(
                    WorkspaceEventType.TOOL_CALL,
                    WorkspaceEventActor.SYSTEM,
                    result_content,
                )
            ]

        # --- result: final session stats ---
        if msg_type == "result":
            stats = data.get("stats", {})
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {
                        "status": "result_received",
                        "result_status": data.get("status", ""),
                        "stats": stats,
                    },
                )
            ]

        return []

    def _flush_delta_buffer(self) -> WorkspaceEvent | None:
        """Flush accumulated delta chunks as a single AGENT_MESSAGE event."""
        if not self._delta_buffer:
            return None
        text = self._delta_buffer
        self._delta_buffer = ""
        return self._make_event(
            WorkspaceEventType.AGENT_MESSAGE,
            WorkspaceEventActor.AGENT,
            {"text": text},
        )

    @staticmethod
    def _classify_tool(tool_name: str) -> WorkspaceEventType:
        """Map a Gemini tool name to the appropriate WorkspaceEventType."""
        if tool_name in ("read_file", "ReadFile", "Read"):
            return WorkspaceEventType.FILE_READ
        if tool_name in ("write_file", "WriteFile", "Write", "edit_file", "Edit", "replace_in_file"):
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
