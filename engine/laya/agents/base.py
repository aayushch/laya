"""CodingAgent abstract protocol for CLI agent adapters."""

from __future__ import annotations

import abc
from typing import AsyncIterator

from laya.models.workspace import SessionStatus, WorkspaceEvent


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
    ) -> None:
        """Spawn the agent subprocess in the target repo directory.

        Args:
            add_dirs: Additional directory paths to include via --add-dir / --include-directories.
        """
        ...

    async def resume_with_answer(self, answer_text: str, add_dirs: list[str] | None = None) -> None:
        """Resume a previous conversation with new user input.

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
