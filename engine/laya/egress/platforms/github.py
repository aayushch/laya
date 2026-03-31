"""GitHub-specific payload normalization and validation."""

from __future__ import annotations


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize GitHub executor payload fields."""
    p = dict(payload)

    # Normalize comment field for comment actions
    if action_type in ("comment", "close_issue") and "comment" not in p:
        p["comment"] = (
            p.pop("body", None)
            or p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or ""
        )

    # Coerce issue_number / pr_number to int
    for key in ("issue_number", "pr_number"):
        if key in p:
            try:
                p[key] = int(p[key])
            except (ValueError, TypeError):
                pass

    # For PR review actions, ensure pr_number is set
    if action_type in ("approve_pr", "request_changes", "merge_pr") and "pr_number" not in p:
        p["pr_number"] = p.pop("issue_number", None) or p.pop("number", None) or 0

    # Merge method default
    if action_type == "merge_pr" and "merge_method" not in p:
        p["merge_method"] = p.pop("merge_strategy", None) or "squash"

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if not payload.get("owner"):
        errors.append("Missing 'owner' (GitHub repository owner)")
    if not payload.get("repo"):
        errors.append("Missing 'repo' (GitHub repository name)")

    if action_type in ("comment", "close_issue", "create_issue"):
        if action_type != "create_issue" and not payload.get("issue_number"):
            errors.append("Missing 'issue_number'")

    if action_type in ("approve_pr", "request_changes", "merge_pr", "comment_pr_line"):
        if not payload.get("pr_number"):
            errors.append("Missing 'pr_number'")

    if action_type == "request_changes":
        if not payload.get("comment"):
            errors.append("Missing 'comment' body for request_changes")

    if action_type == "create_issue":
        if not payload.get("title"):
            errors.append("Missing issue 'title'")

    if action_type == "create_pr":
        if not payload.get("title"):
            errors.append("Missing PR 'title'")
        if not payload.get("head"):
            errors.append("Missing 'head' branch")
        if not payload.get("base"):
            errors.append("Missing 'base' branch")

    return errors
