# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for Action Cards (Stager output + API responses)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Summary written to a card when it is created, before the persona worker and
# stager have produced real content. The pipeline replaces it on emit; anything
# that renders card summaries (for example the agent's CONTEXT.md) treats this
# exact string as "still processing" rather than as content.
PROCESSING_PLACEHOLDER_SUMMARY = "Researching\u2026"


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


# The stager says what each event does to its work item: the pull request,
# ticket, thread, meeting or page the event belongs to. These are the values it
# may return.
#   concludes: the event brings the work item to its end, or reports the end.
#   reopens: the work item had ended and the event puts it back in motion.
#   standalone_notice: a message that only informs and belongs to no work item
#     that can end, such as a newsletter.
#   none: anything else. The work item stays as it was before the event.
STATUS_EFFECTS = ("none", "concludes", "reopens", "standalone_notice")

# The only statuses the stager may assign to a card. Every other status is set
# by the user or by Laya's own code.
LLM_ASSIGNABLE_STATUSES = ("ready", "done")


class CardStatusVerdict(BaseModel):
    """The stager's validated answer about the status of the card it wrote.

    The emit step decides the card's final status from this answer and from the
    state of the work item in the database (see pipeline/card_status.py).
    """

    # One of STATUS_EFFECTS.
    event_effect: str
    # One of LLM_ASSIGNABLE_STATUSES.
    status: str
    # One sentence naming the evidence for event_effect.
    reason: str = ""
    # Earlier cards of the same entity that the stager says this event settles
    # while the work item stays open.
    settled_card_ids: list[str] = Field(default_factory=list)


class ActionCardData(BaseModel):
    """Structured output from the Stager LLM call."""

    header: str = Field(max_length=80)
    summary: str
    intelligence_report: list[str] = Field(default_factory=list)
    staged_output: StagedOutput
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    privacy_tier: int = Field(default=2, ge=1, le=3)
    suggested_tags: list[str] = Field(default_factory=list)
    # Cross-entity context match the stager already identified. Carried through
    # so emit.py can reuse it instead of paying a redundant post-emit ChromaDB
    # search + confirm_context_link LLM call (see code-review-2026-07 §1.1).
    context_match: dict | None = None
    # None when the stager returned no usable status answer, when the feature
    # is switched off, or when this is the fallback card built after a stager
    # parse failure. The card is then created with its default status.
    status_verdict: CardStatusVerdict | None = None


class TagResponse(BaseModel):
    """A tag definition."""

    tag_id: int
    name: str
    color: str | None = None
    is_system: bool = False
    created_at: str | None = None


class TagAssignment(BaseModel):
    """A tag applied to a target (card, entity group, or context group)."""

    tag_id: int
    tag_name: str
    color: str | None = None
    is_system: bool = False
    assigned_by: str = "user"


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
    # Why Laya set this card to done, and who set the current status ("laya" or
    # "user"). Both are None on cards Laya did not close.
    status_reason: str | None = None
    status_set_by: str | None = None
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
    bookmarked_at: str | None = None
    read_at: str | None = None
    group_active_at: str | None = None
    context_id: str | None = None
    last_error: str | None = None
    source_context: str | None = None
    tags: list[TagAssignment] = Field(default_factory=list)


class CardsListResponse(BaseModel):
    """Paginated list of action cards."""

    cards: list[CardResponse]
    total: int
    limit: int
    offset: int


class KeyEvent(BaseModel):
    """A structured key event with separate timestamp."""

    event: str
    timestamp: str = ""


class GroupSummaryResponse(BaseModel):
    """Rolling LLM-generated summary for a multi-card entity group."""

    entity_id: str
    headline: str
    summary: str
    key_events: list[str | KeyEvent] | None = None
    current_status: str | None = None
    pending_actions: list[str] | None = None
    card_count: int
    card_ids: list[str]
    updated_at: str | None = None


class CardGroup(BaseModel):
    """A group of cards that refer to the same entity or semantic context."""

    entity_id: str
    entity_title: str
    entity_url: str | None = None
    platform: str
    card_count: int
    top_priority: str
    latest_at: str
    has_pending: bool
    unread_count: int = 0
    cards: list[CardResponse]
    sort_key: str | None = None
    context_id: str | None = None
    context_label: str | None = None
    platforms: list[str] | None = None  # All distinct platforms in linked groups
    group_summary: GroupSummaryResponse | None = None
    tags: list[TagAssignment] = Field(default_factory=list)
    sub_groups: list["CardGroup"] | None = None


class GroupedCardsResponse(BaseModel):
    """Cards grouped by entity."""

    groups: list[CardGroup]
    total_groups: int
    has_more: bool = False
    date: str | None = None
    prev_date: str | None = None
    next_date: str | None = None
    space_id: str | None = None
