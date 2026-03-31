"""Linear-specific payload normalization and validation."""

from __future__ import annotations


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
