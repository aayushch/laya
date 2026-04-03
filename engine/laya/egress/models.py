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
    """Fields that must be present in payload."""

    optional_fields: list[str] = field(default_factory=list)
    """Fields that may be present."""

    description: str = ""
    """What this action does."""

    confirmation_required: bool = True
    """Whether to show confirmation before executing."""


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
