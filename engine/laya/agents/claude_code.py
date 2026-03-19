"""Claude Code CLI adapter for the CodingAgent protocol."""

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

    def __init__(self, binary_path: str = "claude") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._cc_session_id: str | None = None
        self._repo_path: str = ""
        self._status: SessionStatus = SessionStatus.STARTING

    @property
    def cc_session_id(self) -> str | None:
        """Claude Code's internal session UUID, captured from system.init."""
        return self._cc_session_id

    async def start_session(
        self, session_id: str, prompt: str, repo_path: str, add_dirs: list[str] | None = None,
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
            "--verbose",
            "--permission-mode",
            "plan",
        ]

        if add_dirs:
            for d in add_dirs:
                args.extend(["--add-dir", d])

        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def resume_with_answer(self, answer_text: str, add_dirs: list[str] | None = None) -> None:
        """Resume the Claude Code conversation with the user's answer.

        Spawns a new subprocess using --resume <cc_session_id> so Claude Code
        loads the full conversation history and continues from where it left off.

        Args:
            add_dirs: Extra directory paths to pass via --add-dir flags.
        """
        if not self._cc_session_id:
            raise ValueError("No Claude Code session ID available for resumption")

        self._process = AgentProcess()
        self._status = SessionStatus.STARTING

        args = [
            self._binary,
            "-p",
            answer_text,
            "--resume",
            self._cc_session_id,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "plan",
        ]

        if add_dirs:
            for d in add_dirs:
                args.extend(["--add-dir", d])

        await self._process.spawn(args=args, cwd=self._repo_path)
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
            events = self._parse_stream_json(line)
            if events:
                for event in events:
                    yield event
                continue

            # Fallback: non-JSON output (e.g. approval prompts from non-stream mode)
            if self._is_approval_prompt(line):
                self._status = SessionStatus.AWAITING_INPUT
                yield self._make_event(
                    WorkspaceEventType.APPROVAL_REQUEST,
                    WorkspaceEventActor.AGENT,
                    {"message": line},
                    requires_input=True,
                )
        exit_code = await self._process.wait()

        # If cancel() was already called, honour that status — don't overwrite
        # based on the exit code (SIGKILL gives -9/137, not the clean 143).
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
            # 143 = 128 + 15 (SIGTERM) — clean cancellation
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

    def _parse_stream_json(self, line: str) -> list[WorkspaceEvent]:
        """Parse a stream-json line from Claude Code into workspace events.

        Returns a list because a single assistant/user message can contain
        multiple content blocks (text, tool_use, tool_result, etc.), each
        mapped to its own WorkspaceEvent.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        msg_type = data.get("type", "")

        # --- Skip noise ---
        if msg_type == "rate_limit_event":
            return []

        # --- assistant / user: iterate message.content blocks ---
        if msg_type in ("assistant", "user"):
            msg_id = data.get("message", {}).get("id")
            content_blocks = data.get("message", {}).get("content", [])
            if not isinstance(content_blocks, list):
                return []
            events: list[WorkspaceEvent] = []
            block_idx = 0
            for block in content_blocks:
                block_type = block.get("type")
                # Build a dedup key: msg_id:block_index (unique per content block)
                cc_mid = f"{msg_id}:{block_idx}" if msg_id else None
                block_idx += 1

                if block_type == "text":
                    actor = (
                        WorkspaceEventActor.AGENT
                        if msg_type == "assistant"
                        else WorkspaceEventActor.SYSTEM
                    )
                    events.append(
                        self._make_event(
                            WorkspaceEventType.AGENT_MESSAGE,
                            actor,
                            {"text": block.get("text", "")},
                            agent_message_id=cc_mid,
                        )
                    )
                elif block_type == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})

                    # AskUserQuestion requires user interaction
                    if tool_name == "AskUserQuestion":
                        self._status = SessionStatus.AWAITING_INPUT
                        events.append(
                            self._make_event(
                                WorkspaceEventType.APPROVAL_REQUEST,
                                WorkspaceEventActor.AGENT,
                                {
                                    "ask_user_question": True,
                                    "questions": tool_input.get("questions", []),
                                },
                                requires_input=True,
                                agent_message_id=cc_mid,
                            )
                        )
                    elif tool_name == "ExitPlanMode":
                        # ExitPlanMode contains the final implementation plan
                        plan_text = tool_input.get("plan", "")
                        if plan_text:
                            events.append(
                                self._make_event(
                                    WorkspaceEventType.AGENT_MESSAGE,
                                    WorkspaceEventActor.AGENT,
                                    {"text": plan_text, "is_plan": True},
                                    agent_message_id=cc_mid,
                                )
                            )
                    else:
                        evt_type = self._classify_tool(tool_name)
                        content: dict[str, Any] = {"tool": tool_name, "input": tool_input}
                        if evt_type in (WorkspaceEventType.FILE_READ, WorkspaceEventType.FILE_WRITE):
                            content["file"] = tool_input.get("file_path", "")
                        events.append(
                            self._make_event(evt_type, WorkspaceEventActor.AGENT, content, agent_message_id=cc_mid)
                        )
                # skip: tool_result (verbose tool outputs), thinking
            return events

        # --- system: lightweight metadata ---
        if msg_type == "system":
            subtype = data.get("subtype", "")
            # Capture Claude Code's session ID from the init message
            if subtype == "init":
                cc_sid = data.get("session_id")
                if cc_sid:
                    self._cc_session_id = cc_sid
                    log.info("cc_session_id_captured", cc_session_id=cc_sid)
            meta = {"status": subtype}
            for key in ("model", "cwd", "task_id", "description"):
                if key in data:
                    meta[key] = data[key]
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    meta,
                )
            ]

        # --- result: final session outcome ---
        if msg_type == "result":
            return [
                self._make_event(
                    WorkspaceEventType.STATUS_CHANGE,
                    WorkspaceEventActor.SYSTEM,
                    {"status": "result_received", "result": data.get("result", "")},
                )
            ]

        return []

    @staticmethod
    def _classify_tool(tool_name: str) -> WorkspaceEventType:
        """Map a tool name to the appropriate WorkspaceEventType."""
        if tool_name in ("Read", "ReadFile", "read_file"):
            return WorkspaceEventType.FILE_READ
        if tool_name in ("Write", "WriteFile", "Edit", "edit_file"):
            return WorkspaceEventType.FILE_WRITE
        return WorkspaceEventType.TOOL_CALL

    def _is_approval_prompt(self, text: str) -> bool:
        """Check if a line of text is an approval prompt."""

        # For now, disable approval checks because we are running the claude
        # agent in a --print mode which mostly runs in a non-interactive
        # mode
        # return any(pattern.search(text) for pattern in APPROVAL_PATTERNS)
        return False

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
