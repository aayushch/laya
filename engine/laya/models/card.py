"""Pydantic models for Action Cards (Stager output + API responses)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StagedOutput(BaseModel):
    """Primary deliverable attached to an action card."""

    type: str  # "draft_reply", "code_fix", "briefing", "summary"
    content: str


class SuggestedAction(BaseModel):
    """An action the user can approve/execute from a card."""

    action_id: str
    label: str  # human-readable, e.g. "Post Jira Comment"
    action_type: str  # "comment", "transition", "merge", "send_email"
    target_platform: str  # "jira", "bitbucket", "slack", "gmail"
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionCardData(BaseModel):
    """Structured output from the Stager LLM call."""

    header: str = Field(max_length=80)
    summary: str
    intelligence_report: list[str] = Field(default_factory=list)
    staged_output: StagedOutput
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    privacy_tier: int = Field(default=2, ge=1, le=3)


class CardResponse(BaseModel):
    """Full action card for API responses (mirrors action_cards table)."""

    card_id: str
    event_id: str
    created_at: str | None = None
    priority: str
    persona: str
    category: str
    header: str
    summary: str
    intelligence: list[str] | None = None
    staged_output: StagedOutput | None = None
    suggested_actions: list[SuggestedAction] | None = None
    status: str = "pending"
    privacy_tier: int = 2
    has_workspace: bool = False
    resolved_at: str | None = None
    user_feedback: str | None = None
    feedback_type: str | None = None
    confidence: float | None = None
    router_model: str | None = None
    stager_model: str | None = None
    updated_at: str | None = None


class CardsListResponse(BaseModel):
    """Paginated list of action cards."""

    cards: list[CardResponse]
    total: int
    limit: int
    offset: int
