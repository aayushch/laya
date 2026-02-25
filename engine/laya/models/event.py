"""Pydantic models for the Laya Event schema."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventSource(BaseModel):
    platform: str = Field(..., pattern=r"^(jira|bitbucket|slack|gmail|calendar|github|laya)$")
    connection_id: str | None = None
    raw_event_type: str


class EventActor(BaseModel):
    name: str
    email: str
    platform_handle: str | None = None


class EventSubject(BaseModel):
    type: str = Field(..., pattern=r"^(ticket|pull_request|build|thread|email_thread|meeting|briefing)$")
    id: str
    title: str
    url: str | None = None


class EventContent(BaseModel):
    body: str
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LayaEvent(BaseModel):
    """Inbound event from n8n, normalized to the Laya Event schema."""

    event_id: str
    timestamp: datetime
    source: EventSource
    actor: EventActor
    subject: EventSubject
    content: EventContent


class EventResponse(BaseModel):
    """Response after accepting an event."""

    event_id: str
    status: str = "queued"
