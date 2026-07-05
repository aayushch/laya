# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""CodingAgent abstract protocol for CLI agent adapters."""

from __future__ import annotations

import abc
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from laya.models.workspace import (
    SessionStatus,
    WorkspaceEvent,
    WorkspaceEventActor,
    WorkspaceEventType,
)

if TYPE_CHECKING:
    from laya.agents.subprocess_helper import AgentProcess


class CodingAgent(abc.ABC):
    """Abstract protocol for CLI coding agent adapters.

    Each adapter (Claude Code, Gemini CLI, Codex CLI) implements this interface
    to spawn and manage an interactive subprocess.
    """

    @abc.abstractmethod
    async def start_session(
        self,
        session_id: str,
        prompt: str,
        repo_path: str,
        add_dirs: list[str] | None = None,
        mode: str | None = None,
        research: bool = False,
        space_id: str | None = None,
    ) -> None:
        """Spawn the agent subprocess in the target repo directory.

        Args:
            add_dirs: Additional directory paths to include via --add-dir / --include-directories.
            mode: Agent-specific permission/sandbox mode override.
            research: If True, enable web search and file write permissions for
                research-oriented tasks. Each adapter translates this into the
                appropriate CLI flags.
            space_id: Laya space context. Used by adapters that configure
                callback MCP access so tool calls are scoped to the user's space.
        """
        ...

    async def resume_with_answer(
        self,
        answer_text: str,
        add_dirs: list[str] | None = None,
        research: bool = False,
        mode: str | None = None,
        space_id: str | None = None,
    ) -> None:
        """Resume a previous conversation with new user input.

        Args:
            add_dirs: Extra directory paths to include.
            research: If True, apply research-mode permissions (scoped writes + web)
                instead of full edit access.
            mode: Explicit permission mode override (e.g. 'full' for bash access).
            space_id: Laya space context (see start_session).

        Not all agents support this — the default raises NotImplementedError.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support session resumption")

    @abc.abstractmethod
    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        """Yield WorkspaceEvent objects as the agent produces output."""
        ...

    @abc.abstractmethod
    async def send_input(self, text: str) -> None:
        """Send user input to the agent's stdin."""
        ...

    @abc.abstractmethod
    async def pause(self) -> None:
        """Pause the agent subprocess."""
        ...

    @abc.abstractmethod
    async def resume(self) -> None:
        """Resume the agent subprocess."""
        ...

    @abc.abstractmethod
    async def cancel(self) -> None:
        """Terminate the agent subprocess."""
        ...

    @abc.abstractmethod
    def get_status(self) -> SessionStatus:
        """Return the current session status."""
        ...


class BaseCodingAgent(CodingAgent):
    """Shared lifecycle for CLI agent adapters.

    The four adapters (Claude Code, Codex, Gemini, Pi) had byte-identical copies
    of event construction, the control methods, and the exit-code → status
    epilogue (~380 lines total) which had already drifted. Those live here now so
    they're single-sourced; subclasses provide only what actually differs: the
    spawn arguments (start_session / resume_with_answer), the stream-json line
    parsing, and their stream_events read loop (which calls
    ``_terminal_status_event`` for the epilogue). Review §5.2 — P7-3.

    Subclasses must set ``_session_id``, ``_process`` and ``_status`` in __init__.
    """

    _session_id: str
    _process: "AgentProcess"
    _status: SessionStatus

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

    def _on_process_exit(self) -> None:
        """Hook invoked once the subprocess has exited, before the status is
        finalized. Default no-op; Claude Code overrides it to unlink the 0600
        MCP-config temp files it spawned with."""

    async def cancel(self) -> None:
        await self._process.terminate()
        self._on_process_exit()
        self._status = SessionStatus.CANCELLED

    def get_status(self) -> SessionStatus:
        return self._status

    def _terminal_status_event(self, exit_code: int) -> WorkspaceEvent:
        """Map the subprocess exit code to the final status + terminal event.

        Call once from a subclass's stream_events after ``self._process.wait()``.
        """
        self._on_process_exit()

        # If cancel() was already called, honour that status — don't overwrite
        # based on the exit code (SIGKILL gives -9/137, not the clean 143).
        if self._status == SessionStatus.CANCELLED:
            return self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "cancelled", "exit_code": exit_code},
            )
        if exit_code == 0:
            self._status = SessionStatus.COMPLETED
            return self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "completed", "exit_code": exit_code},
            )
        if exit_code in (143, -15):
            # 143 = 128 + 15 (SIGTERM) — clean cancellation
            self._status = SessionStatus.CANCELLED
            return self._make_event(
                WorkspaceEventType.STATUS_CHANGE,
                WorkspaceEventActor.SYSTEM,
                {"status": "cancelled", "exit_code": exit_code},
            )
        self._status = SessionStatus.FAILED
        stderr = self._process.stderr_output
        error_msg = f"Agent exited with code {exit_code}"
        if stderr:
            error_msg += f": {stderr}"
        return self._make_event(
            WorkspaceEventType.ERROR,
            WorkspaceEventActor.SYSTEM,
            {"error": error_msg, "exit_code": exit_code},
        )
