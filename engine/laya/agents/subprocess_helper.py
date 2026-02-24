"""Cross-platform async subprocess management for coding agent sessions.

Uses asyncio.create_subprocess_exec with PIPE for stdin/stdout/stderr.
Works on macOS, Linux, and Windows.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from typing import AsyncIterator

import structlog

log = structlog.get_logger()

# Regex to strip ANSI escape codes from terminal output
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


class AgentProcess:
    """Manages a single async subprocess for a coding agent."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._running: bool = False
        self._paused: bool = False

    async def spawn(
        self,
        args: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Spawn an async subprocess.

        Args:
            args: Command and arguments, e.g. ["claude", "-p", "Fix the NPE..."]
            cwd: Working directory (the repo path).
            env: Optional environment overrides (merged with os.environ).
        """
        spawn_env = {**os.environ}
        if env:
            spawn_env.update(env)

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
            cwd=cwd,
            env=spawn_env,
        )
        self._running = True
        log.info("agent_process_spawned", pid=self._process.pid, command=args[0])

    async def read_lines(self) -> AsyncIterator[str]:
        """Async generator yielding lines from stdout."""
        if self._process is None or self._process.stdout is None:
            return

        while True:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    # EOF — process has exited
                    self._running = False
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\n\r")
                yield decoded
            except Exception:
                self._running = False
                break

    async def write(self, text: str) -> None:
        """Write text to the subprocess stdin."""
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.write((text + "\n").encode("utf-8"))
            await self._process.stdin.drain()

    async def pause(self) -> None:
        """Pause the subprocess (SIGSTOP on Unix, no-op on Windows)."""
        if self._process is None or self._process.pid is None:
            return

        if sys.platform != "win32":
            try:
                os.kill(self._process.pid, signal.SIGSTOP)
                self._paused = True
                log.info("agent_process_paused", pid=self._process.pid)
            except ProcessLookupError:
                pass
        else:
            log.warning("pause_not_supported_windows", pid=self._process.pid)

    async def resume(self) -> None:
        """Resume the subprocess (SIGCONT on Unix, no-op on Windows)."""
        if self._process is None or self._process.pid is None:
            return

        if sys.platform != "win32":
            try:
                os.kill(self._process.pid, signal.SIGCONT)
                self._paused = False
                log.info("agent_process_resumed", pid=self._process.pid)
            except ProcessLookupError:
                pass
        else:
            log.warning("resume_not_supported_windows", pid=self._process.pid)

    async def terminate(self) -> None:
        """Terminate the subprocess. SIGTERM then SIGKILL after 5s."""
        if self._process is None:
            return

        self._running = False
        try:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass
        log.info("agent_process_terminated", pid=self._process.pid)

    async def wait(self) -> int:
        """Wait for process completion and return exit code."""
        if self._process is None:
            return -1
        code = await self._process.wait()
        self._running = False
        return code

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None
