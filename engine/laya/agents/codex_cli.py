"""OpenAI Codex CLI adapter for the CodingAgent protocol."""

from __future__ import annotations

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

APPROVAL_PATTERNS = [
    re.compile(r"Do you want to (proceed|continue)\?", re.IGNORECASE),
    re.compile(r"\[Y/n\]", re.IGNORECASE),
    re.compile(r"\(y/N\)", re.IGNORECASE),
]


class CodexCliAgent(CodingAgent):
    """OpenAI Codex CLI adapter. Spawns `codex` as a subprocess."""

    def __init__(self, binary_path: str = "codex") -> None:
        self._binary = binary_path
        self._process = AgentProcess()
        self._session_id: str = ""
        self._status: SessionStatus = SessionStatus.STARTING

    async def start_session(self, session_id: str, prompt: str, repo_path: str) -> None:
        self._session_id = session_id
        self._status = SessionStatus.STARTING
        args = [self._binary, prompt]
        await self._process.spawn(args=args, cwd=repo_path)
        self._status = SessionStatus.RUNNING

    async def stream_events(self) -> AsyncIterator[WorkspaceEvent]:
        yield WorkspaceEvent(
            event_id=f"we_{uuid.uuid4().hex[:12]}",
            session_id=self._session_id,
            event_type=WorkspaceEventType.STATUS_CHANGE,
            actor=WorkspaceEventActor.SYSTEM,
            content={"status": "running", "agent": "codex_cli"},
        )

        async for raw_line in self._process.read_lines():
            line = strip_ansi(raw_line).strip()
            if not line:
                continue

            if any(p.search(line) for p in APPROVAL_PATTERNS):
                self._status = SessionStatus.AWAITING_INPUT
                yield WorkspaceEvent(
                    event_id=f"we_{uuid.uuid4().hex[:12]}",
                    session_id=self._session_id,
                    event_type=WorkspaceEventType.APPROVAL_REQUEST,
                    actor=WorkspaceEventActor.AGENT,
                    content={"message": line},
                    requires_input=True,
                )
            else:
                yield WorkspaceEvent(
                    event_id=f"we_{uuid.uuid4().hex[:12]}",
                    session_id=self._session_id,
                    event_type=WorkspaceEventType.AGENT_MESSAGE,
                    actor=WorkspaceEventActor.AGENT,
                    content={"text": line},
                )

        exit_code = await self._process.wait()
        self._status = SessionStatus.COMPLETED if exit_code == 0 else SessionStatus.FAILED
        yield WorkspaceEvent(
            event_id=f"we_{uuid.uuid4().hex[:12]}",
            session_id=self._session_id,
            event_type=WorkspaceEventType.STATUS_CHANGE,
            actor=WorkspaceEventActor.SYSTEM,
            content={"status": self._status.value, "exit_code": exit_code},
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
