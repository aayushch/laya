"""Pydantic models for the Laya Event schema."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EventSource(BaseModel):
    platform: str = Field(..., min_length=1, max_length=64)
    connection_id: str | None = None
    raw_event_type: str


class EventActor(BaseModel):
    name: str
    email: str
    platform_handle: str | None = None

    @field_validator("name", "email", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        """n8n Set node converts empty-string expressions to null — coerce back."""
        return v if isinstance(v, str) else ""


class EventSubject(BaseModel):
    type: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,31}$", min_length=1)
    id: str
    title: str
    url: str | None = None

    @field_validator("title", "id", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        return v if isinstance(v, str) else ""


class EventContent(BaseModel):
    body: str
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("body", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        return v if isinstance(v, str) else ""


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
