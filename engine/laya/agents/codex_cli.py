"""OpenAI Codex CLI adapter for the CodingAgent protocol."""

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


class CodexCliAgent(CodingAgent):
    """OpenAI Codex CLI adapter.

    Spawns ``codex exec --json "<prompt>"`` as a subprocess and parses
    the JSONL event stream.  Supports ``--add-dir``, session resumption
    via ``codex exec resume <thread_id>``, and a sandbox permission flip
    (read-only on first run, --full-auto on resume).
    """

    def __init__(self, binary_path: str = "codex") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._thread_id: str | None = None
        self._repo_path: str = ""
        self._status: SessionStatus = SessionStatus.STARTING

    @property
    def cc_session_id(self) -> str | None:
        """Codex's thread_id, stored in the generic cc_session_id column."""
        return self._thread_id

    async def start_session(
        self, session_id: str, prompt: str, repo_path: str, add_dirs: list[str] | None = None,
        mode: str | None = None, research: bool = False,
    ) -> None:
        self._session_id = session_id
        self._repo_path = repo_path
        self._status = SessionStatus.STARTING

        # Research mode: workspace-write sandbox enforces writes at the OS level
        # (Seatbelt on macOS, Landlock on Linux) — only the cwd (research dir)
        # is writable.  Normal mode defaults to read-only.
        sandbox_mode = mode or ("workspace-write" if research else "read-only")

        args = [
            self._binary,
            "exec",
            "--json",
            "--sandbox",
            sandbox_mode,
            prompt,
        ]

        if research:
            # Enable live web search for research tasks
            args.append("--search")

        if add_dirs:
            for d in add_dirs:
                args.extend(["--add-dir", d])

        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def resume_with_answer(self, answer_text: str, add_dirs: list[str] | None = None, research: bool = False) -> None:
        """Resume the Codex conversation with new instructions.

        Spawns ``codex exec resume <thread_id> "<prompt>" --json`` with
        appropriate sandbox mode: workspace-write for research (OS-level
        scoping to cwd), full-auto for code tasks.
        """
        if not self._thread_id:
            raise ValueError("No Codex thread ID available for resumption")

        self._process = AgentProcess()
        self._status = SessionStatus.STARTING

        args = [
            self._binary,
            "exec",
            "resume",
            self._thread_id,
            answer_text,
            "--json",
        ]

        if research:
            args.extend(["--sandbox", "workspace-write", "--search"])
        else:
            args.append("--full-auto")

        if add_dirs:
            for d in add_dirs:
                args.extend(["--add-dir", d])

        await self._process.spawn(args=args, cwd=self._repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Parse Codex exec JSONL output into WorkspaceEvents."""
        yield self._make_event(
            WorkspaceEventType.STATUS_CHANGE,
            WorkspaceEventActor.SYSTEM,
            {"status": "running", "agent": "codex_cli"},
        )

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            # Try JSON parsing first (--json mode)
            events = self._parse_jsonl(line)
            if events is not None:
                for event in events:
                    yield event
                continue

            # Fallback: non-JSON stderr leak or approval prompt
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
    # JSONL parsing
    # ------------------------------------------------------------------

    def _parse_jsonl(self, line: str) -> list[WorkspaceEvent] | None:
        """Parse a single JSONL line from ``codex exec --json``.

        Returns a list of WorkspaceEvents, an empty list if parsed but no
        events to emit, or ``None`` if the line is not valid JSON.

        Codex event types:
        - thread.started: ``{thread_id}`` — session ID for resumption
        - turn.started / turn.completed: turn boundaries
        - item.started / item.completed / item.updated: item lifecycle
        - error: agent-level error

        Item types (nested in item.* events):
        - agent_message: ``{text}``
        - command_execution: ``{command, aggregated_output, exit_code, status}``
        - file_edit / file_create / file_delete: file changes
        - todo_list: ``{items: [{text, completed}]}``
        - reasoning: internal reasoning
        - mcp_tool_call / web_search: tool invocations
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")

        # --- thread.started: capture thread_id for resumption ---
        if event_type == "thread.started":
            tid = data.get("thread_id")
            if tid:
                self._thread_id = tid
                log.info("codex_thread_id_captured", thread_id=tid)
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "init", "thread_id": tid},
                )
            ]

        # --- turn boundaries ---
        if event_type == "turn.started":
            return []

        if event_type == "turn.completed":
            usage = data.get("usage", {})
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "turn_completed", "usage": usage},
                )
            ]

        # --- item events ---
        if event_type in ("item.started", "item.completed", "item.updated"):
            item = data.get("item", {})
            if not item:
                return []
            return self._parse_item(item, event_type=event_type)

        # --- error ---
        if event_type == "error":
            return [
                self._make_event(
                    WorkspaceEventType.ERROR,
                    WorkspaceEventActor.SYSTEM,
                    {"error": data.get("message", str(data))},
                )
            ]

        # Unknown event type — skip silently
        return []

    def _parse_item(self, item: dict[str, Any], *, event_type: str) -> list[WorkspaceEvent]:
        """Convert a Codex item dict into WorkspaceEvent(s).

        For command_execution, only emit on item.completed (with output)
        to avoid duplicate events for the same item.  For other types,
        emit on item.completed or item.updated, skip item.started.
        """
        item_type = item.get("type", "")
        item_id = item.get("id")
        is_final = event_type == "item.completed"

        # --- agent_message: emit on completed only ---
        if item_type == "agent_message":
            if not is_final:
                return []
            text = item.get("text", "")
            if not text:
                return []
            return [
                self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": text},
                    agent_message_id=item_id,
                )
            ]

        # --- command_execution: emit on completed only (with output) ---
        if item_type == "command_execution":
            if not is_final:
                return []
            content: dict[str, Any] = {
                "tool": "shell",
                "command": item.get("command", ""),
                "output": item.get("aggregated_output", ""),
                "exit_code": item.get("exit_code"),
                "status": item.get("status", ""),
            }
            return [
                self._make_event(
                    WorkspaceEventType.TOOL_CALL,
                    WorkspaceEventActor.AGENT,
                    content,
                    agent_message_id=item_id,
                )
            ]

        # --- file operations (emit on completed only) ---
        if item_type in ("file_edit", "file_create"):
            if not is_final:
                return []
            content = {
                "tool": item_type,
                "file": item.get("path", item.get("file", "")),
            }
            if "diff" in item:
                content["diff"] = item["diff"]
            return [
                self._make_event(
                    WorkspaceEventType.FILE_WRITE,
                    WorkspaceEventActor.AGENT,
                    content,
                    agent_message_id=item_id,
                )
            ]

        if item_type == "file_delete":
            if not is_final:
                return []
            return [
                self._make_event(
                    WorkspaceEventType.FILE_WRITE,
                    WorkspaceEventActor.AGENT,
                    {"tool": "file_delete", "file": item.get("path", item.get("file", ""))},
                    agent_message_id=item_id,
                )
            ]

        if item_type == "file_read":
            if not is_final:
                return []
            return [
                self._make_event(
                    WorkspaceEventType.FILE_READ,
                    WorkspaceEventActor.AGENT,
                    {"tool": "file_read", "file": item.get("path", item.get("file", ""))},
                    agent_message_id=item_id,
                )
            ]

        # --- todo_list: emit on completed and updated ---
        if item_type == "todo_list":
            if event_type == "item.started":
                return []
            items = item.get("items", [])
            summary_parts = []
            for t in items:
                mark = "x" if t.get("completed") else " "
                summary_parts.append(f"[{mark}] {t.get('text', '')}")
            return [
                self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": "\n".join(summary_parts), "is_todo": True},
                    agent_message_id=item_id,
                )
            ]

        # --- reasoning ---
        if item_type == "reasoning":
            # Internal reasoning — skip (similar to Claude's thinking blocks)
            return []

        # --- MCP tool calls / web search ---
        if item_type in ("mcp_tool_call", "web_search"):
            if not is_final:
                return []
            return [
                self._make_event(
                    WorkspaceEventType.TOOL_CALL,
                    WorkspaceEventActor.AGENT,
                    {"tool": item_type, "input": item},
                    agent_message_id=item_id,
                )
            ]

        # Unknown item type — emit on completed only if there's text
        if not is_final:
            return []
        text = item.get("text", "")
        if text:
            return [
                self._make_event(
                    WorkspaceEventType.AGENT_MESSAGE,
                    WorkspaceEventActor.AGENT,
                    {"text": text, "item_type": item_type},
                    agent_message_id=item_id,
                )
            ]

        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
