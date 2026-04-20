"""Data models for the Laya egress system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class EgressRequest:
    """A request to perform an outbound action on an external platform."""

    platform: str
    """Platform identifier: gmail, jira, slack, github, bitbucket, outlook, linear, calendar, smtp."""

    action_type: str
    """Action to perform: send_email, comment, transition, approve_pr, merge_pr, etc."""

    payload: dict
    """Platform-specific action data (body, to, issue_key, channel, etc.)."""

    connection_id: str | None = None
    """Specific connection to use (when user picks a From account)."""

    source_card_id: str | None = None
    """Card that triggered this action (for context and logging)."""

    source_event_id: str | None = None
    """Original event (for metadata like thread_id, issue_key)."""

    space_id: str | None = None
    """Space for credential and executor resolution."""

    dry_run: bool = False
    """Validate without executing."""


@dataclass
class EgressResult:
    """Result of an egress action execution."""

    success: bool
    result_url: str | None = None
    """Link to the created/modified resource on the platform."""

    result_data: dict = field(default_factory=dict)
    """Platform-specific response data."""

    error: str | None = None
    """Error message if the action failed."""

    retryable: bool = False
    """Whether the action can be retried (transient failure)."""


@dataclass
class EgressPreview:
    """Preview of what an egress action will do, shown before confirmation."""

    platform: str
    action_type: str

    summary: str
    """Human-readable summary: 'Send email to sarah@co.com with subject Re: Nav redesign'."""

    details: dict = field(default_factory=dict)
    """Structured preview data for UI rendering."""

    warnings: list[str] = field(default_factory=list)
    """Warnings to show the user: 'This will send to 5 recipients'."""

    estimated_impact: str = "low"
    """Impact level: low, medium, high."""


@dataclass
class EgressCapability:
    """Describes one action a platform can perform."""

    action_type: str
    """Action identifier: comment, send_email, approve_pr, etc."""

    label: str
    """Human-readable button text: 'Post Comment', 'Send Email'."""

    requires_fields: list[str] = field(default_factory=list)
    """All fields the executor needs present in the final payload.

    Includes both engine-derivable identifiers (owner, repo, issue_key, …)
    and LLM-emitted content fields. Used by UI/connection surfaces to
    describe full platform requirements. See ``content_fields`` for the
    LLM-facing subset."""

    optional_fields: list[str] = field(default_factory=list)
    """Optional fields the executor accepts (identifier + content mixed).
    See ``optional_content_fields`` for the LLM-facing subset."""

    content_fields: list[str] = field(default_factory=list)
    """Required fields the LLM must emit in the action payload.

    Excludes identifier fields that the engine fills from the source
    event (owner, repo, issue_key, gmail_id, channel, …).  Rendered into
    the stager's [SUPPORTED ACTIONS] block so the LLM only sees content
    fields it's responsible for producing."""

    optional_content_fields: list[str] = field(default_factory=list)
    """Optional content fields the LLM may emit."""

    description: str = ""
    """What this action does."""

    confirmation_required: bool = True
    """Whether to show confirmation before executing."""

    summary_template: str | None = None
    """Preview summary template. Rendered with ``str.format_map`` against the
    enriched payload (plus computed placeholders: ``{gh_ref}``, ``{bb_ref}``).
    Missing fields render as ``"unknown"``. When ``None``, preview falls back
    to ``"<label> on <platform>"``."""

    warnings: list[str] = field(default_factory=list)
    """Static warning strings always shown in the preview for this action.
    Dynamic warnings (e.g. terminal-status transitions, many-recipient email)
    are computed in ``router.py`` because they depend on runtime payload
    values."""

    impact: str = "low"
    """Estimated blast radius for the preview UI: ``"low"`` | ``"medium"`` |
    ``"high"``."""


@dataclass
class Connection:
    """A configured platform connection."""

    connection_id: str
    platform: str
    name: str
    status: str = "connected"
    """connected | error | expired."""

    capabilities: list[str] = field(default_factory=list)
    """Action types available through this connection."""

    n8n_credential_id: str | None = None
    space_id: str | None = None
    error_message: str | None = None
    last_validated_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ConnectionResult:
    """Result of connecting a platform."""

    status: str
    """connected | failed."""

    connection_id: str | None = None
    capabilities: list[str] = field(default_factory=list)
    error: str | None = None
