"""Jira-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive Jira identifiers from the source event.

    ``subject_id`` holds the canonical issue key (e.g. ``PROJ-123``) that
    every Jira action except ``create_issue`` needs.  ``jira_project`` in
    the metadata covers the create path.
    """
    ids: dict = {}
    subject_id = (event_row or {}).get("subject_id")
    if subject_id:
        ids["issue_key"] = subject_id
    project = (content_metadata or {}).get("jira_project")
    if project:
        ids["project"] = project
    return ids


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Jira executor payload fields."""
    p = dict(payload)

    # Normalize issue key field name
    if "issue_key" not in p:
        p["issue_key"] = (
            p.pop("issueKey", None)
            or p.pop("key", None)
            or p.pop("ticket_id", None)
            or ""
        )

    # Normalize comment field
    if action_type == "comment" and "comment" not in p:
        p["comment"] = (
            p.pop("body", None)
            or p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or p.pop("raw", None)
            or ""
        )

    # Normalize create_issue fields
    if action_type == "create_issue":
        if "summary" not in p:
            p["summary"] = p.pop("title", None) or ""
        if "type" not in p:
            p["type"] = p.pop("issueType", None) or p.pop("issue_type", None) or "Task"

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if action_type in ("comment", "transition", "assign", "update_fields"):
        if not payload.get("issue_key"):
            errors.append("Missing 'issue_key' (e.g., 'PROJ-123')")

    if action_type == "comment":
        if not payload.get("comment"):
            errors.append("Missing 'comment' body")

    if action_type == "transition":
        if not payload.get("target_status"):
            errors.append("Missing 'target_status' (e.g., 'Done', 'In Progress')")

    if action_type == "create_issue":
        if not payload.get("project"):
            errors.append("Missing 'project' key (e.g., 'PROJ')")
        if not payload.get("summary"):
            errors.append("Missing issue 'summary' / title")

    if action_type == "assign":
        if not payload.get("assignee"):
            errors.append("Missing 'assignee'")

    return errors
