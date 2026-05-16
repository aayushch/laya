"""Pydantic models for workspace sessions and events."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentType(str, Enum):
    CLAUDE_CODE = "claude_code"
    GEMINI_CLI = "gemini_cli"
    CODEX_CLI = "codex_cli"
    PI_CLI = "pi_cli"


class SessionStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkspaceEventType(str, Enum):
    AGENT_MESSAGE = "agent_message"
    USER_RESPONSE = "user_response"
    TOOL_CALL = "tool_call"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    STATUS_CHANGE = "status_change"
    ERROR = "error"
    QUESTIONS_DISMISSED = "questions_dismissed"


class WorkspaceEventActor(str, Enum):
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


class WorkspaceSession(BaseModel):
    """An agent session within a card workspace."""

    session_id: str
    card_id: str
    agent_type: AgentType
    status: SessionStatus = SessionStatus.STARTING
    repo_path: str | None = None
    initial_prompt: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    findings_json: dict[str, Any] | None = None
    error_message: str | None = None


class WorkspaceEvent(BaseModel):
    """A single event within a workspace session timeline."""

    event_id: str
    session_id: str
    timestamp: datetime | None = None
    event_type: WorkspaceEventType
    actor: WorkspaceEventActor
    content: dict[str, Any] = Field(default_factory=dict)
    requires_input: bool = False
    agent_message_id: str | None = None
