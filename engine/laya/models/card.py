"""Pydantic models for Action Cards (Stager output + API responses)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StagedOutput(BaseModel):
    """Primary deliverable attached to an action card."""

    type: str  # "draft_reply", "code_fix", "briefing", "summary", "status_update"
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
    entity_id: str | None = None
    source_ref: str | None = None
    source_url: str | None = None
    selected_action_id: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    space_id: str | None = None
    space_name: str | None = None
    space_color: str | None = None


class CardsListResponse(BaseModel):
    """Paginated list of action cards."""

    cards: list[CardResponse]
    total: int
    limit: int
    offset: int


class CardGroup(BaseModel):
    """A group of cards that refer to the same entity (e.g. a Jira ticket)."""

    entity_id: str
    entity_title: str
    entity_url: str | None = None
    platform: str
    card_count: int
    top_priority: str
    latest_at: str
    has_pending: bool
    cards: list[CardResponse]
    sort_key: str | None = None


class GroupedCardsResponse(BaseModel):
    """Cards grouped by entity."""

    groups: list[CardGroup]
    total_groups: int
    date: str | None = None
    prev_date: str | None = None
    next_date: str | None = None
    space_id: str | None = None
