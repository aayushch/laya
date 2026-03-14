"""Base worker interface and shared utilities."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkerResult(BaseModel):
    """Structured output from any worker."""

    persona: str
    findings: dict[str, Any] = Field(default_factory=dict)
    drafted_output: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    card_status: str | None = None
    error: str | None = None
