# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

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

    @property
    def entity_id(self) -> str:
        """Canonical grouping key for this event's work item — the one true
        spelling, shared by every pipeline stage (emit, queue, stager) so they
        never disagree. Relies on n8n delivering a canonical ``subject.id``
        (e.g. Gmail's thread root); per-platform shape correction lives in the
        ingestion workflows, not here."""
        return f"{self.source.platform}:{self.subject.type}:{self.subject.id}"

    @property
    def is_terminal(self) -> bool:
        """True when this event means the work item has completed, so pending
        sibling cards in the same entity group can be auto-resolved. The set of
        terminal event types per platform lives in the egress platform registry
        (the single source of truth for everything platform). Imported lazily to
        avoid a models→egress import-time cycle."""
        from laya.egress.registry import is_terminal_event

        return is_terminal_event(self.source.platform, self.source.raw_event_type)


class EventResponse(BaseModel):
    """Response after accepting an event."""

    event_id: str
    status: str = "queued"
