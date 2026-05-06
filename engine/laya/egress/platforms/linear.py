"""Linear-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

import re

_EVENT_ID_RE = re.compile(r"^evt_linear_(?P<id>[^_]+)_\d+$")


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive Linear identifiers from the event.

    ``issue_id`` is the Linear UUID encoded in the event_id.  ``team_id``
    comes from ``content_metadata['linear_team_id']`` (emitted by the
    ingestion workflow alongside the team key).
    """
    ids: dict = {}
    if event_id:
        m = _EVENT_ID_RE.match(event_id)
        if m:
            ids["issue_id"] = m.group("id")
    team_id = (content_metadata or {}).get("linear_team_id")
    if team_id:
        ids["team_id"] = team_id
    return ids


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Linear executor payload fields."""
    p = dict(payload)

    # Normalize issue ID
    if "issue_id" not in p:
        p["issue_id"] = p.pop("ticket_id", None) or p.pop("id", None) or ""

    # Create issue: normalize team_id
    if action_type == "create_issue" and "team_id" not in p:
        p["team_id"] = p.pop("project", None) or p.pop("team", None) or ""

    # Normalize body/comment
    if action_type == "comment" and "body" not in p:
        p["body"] = (
            p.pop("comment", None)
            or p.pop("message", None)
            or p.pop("content", None)
            or p.pop("raw", None)
            or ""
        )

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if action_type in ("comment", "update_status", "assign"):
        if not payload.get("issue_id"):
            errors.append("Missing 'issue_id'")

    if action_type == "comment":
        if not payload.get("body"):
            errors.append("Missing comment 'body'")

    if action_type == "create_issue":
        if not payload.get("team_id"):
            errors.append("Missing 'team_id'")
        if not payload.get("title"):
            errors.append("Missing issue 'title'")

    if action_type == "update_status":
        if not payload.get("state_id"):
            errors.append("Missing 'state_id'")

    if action_type == "assign":
        if not payload.get("assignee_id"):
            errors.append("Missing 'assignee_id'")

    return errors
