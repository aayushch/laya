# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Cross-platform async subprocess management for coding agent sessions.

Uses asyncio.create_subprocess_exec with PIPE for stdout.
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
        self._stderr_task: asyncio.Task | None = None
        self._stderr_lines: list[str] = []

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
        from laya.config import _augmented_path

        spawn_env = {**os.environ, "PATH": _augmented_path()}
        if env:
            spawn_env.update(env)

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=spawn_env,
        )
        self._running = True

        # Drain stderr in the background so the pipe never blocks.
        # Lines are logged via structlog at debug level.
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(command=args[0]),
            name=f"stderr-drain-{self._process.pid}",
        )

        log.info("agent_process_spawned", pid=self._process.pid, command=args[0])

    async def _drain_stderr(self, command: str) -> None:
        """Read stderr lines and forward them to the log file.

        Lines are collected so they can be retrieved after the process exits
        (e.g. to include in error messages).
        """
        if self._process is None or self._process.stderr is None:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = strip_ansi(line.decode("utf-8", errors="replace").rstrip("\n\r"))
                if text:
                    self._stderr_lines.append(text)
                    log.warning("agent_stderr", command=command, pid=self._process.pid, line=text)
        except Exception:
            pass  # Process exited or pipe broken — nothing to do

    async def read_lines(self, idle_timeout: float = 300.0) -> AsyncIterator[str]:
        """Async generator yielding lines from stdout.

        Args:
            idle_timeout: Max seconds with no output before killing the process.
                          Defends against hung API calls in the child. Default 5 min.
        """
        if self._process is None or self._process.stdout is None:
            return

        line_count = 0
        last_output = asyncio.get_event_loop().time()

        while True:
            try:
                try:
                    line = await asyncio.wait_for(
                        self._process.stdout.readline(), timeout=15.0
                    )
                except asyncio.TimeoutError:
                    now = asyncio.get_event_loop().time()
                    alive = self._process.returncode is None
                    idle_secs = round(now - last_output)
                    if not alive:
                        self._running = False
                        break
                    if idle_secs >= idle_timeout:
                        log.warning(
                            "agent_idle_timeout",
                            pid=self._process.pid,
                            idle_secs=idle_secs,
                            lines_read=line_count,
                        )
                        self._process.kill()
                        self._running = False
                        break
                    continue

                if not line:
                    # EOF — process has exited
                    self._running = False
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\n\r")
                line_count += 1
                last_output = asyncio.get_event_loop().time()
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
            except ProcessLookupError:
                pass

    async def resume(self) -> None:
        """Resume the subprocess (SIGCONT on Unix, no-op on Windows)."""
        if self._process is None or self._process.pid is None:
            return

        if sys.platform != "win32":
            try:
                os.kill(self._process.pid, signal.SIGCONT)
                self._paused = False
            except ProcessLookupError:
                pass

    async def terminate(self) -> None:
        """Terminate the subprocess. SIGTERM then SIGKILL after 5s."""
        if self._process is None:
            return

        self._running = False
        pid = self._process.pid
        try:
            if self._process.stdout:
                self._process.stdout.feed_eof()
            if self._process.stderr:
                self._process.stderr.feed_eof()
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except ProcessLookupError:
            pass
        except asyncio.TimeoutError:
            pass

        # Clean up the stderr drain task
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass

        log.info("agent_process_terminated", pid=pid)

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

    @property
    def stderr_output(self) -> str:
        """Return collected stderr lines as a single string (last 50 lines max)."""
        return "\n".join(self._stderr_lines[-50:])
