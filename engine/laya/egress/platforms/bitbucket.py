"""Bitbucket-specific payload normalization and validation."""

from __future__ import annotations


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Bitbucket executor payload fields."""
    p = dict(payload)

    # Normalize comment field
    if action_type == "comment_pr" and "comment" not in p:
        p["comment"] = (
            p.pop("body", None)
            or p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or ""
        )

    # Normalize comment_id for thread replies (may come as int or string)
    if "comment_id" in p and p["comment_id"]:
        p["comment_id"] = str(p["comment_id"])

    # Normalize PR ID
    if "pr_id" not in p:
        p["pr_id"] = p.pop("pr_number", None) or p.pop("number", None) or ""

    # Coerce pr_id to string
    if p.get("pr_id") is not None:
        p["pr_id"] = str(p["pr_id"])

    # Merge strategy default
    if action_type == "merge_pr" and "merge_strategy" not in p:
        p["merge_strategy"] = "squash"

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if not payload.get("workspace"):
        errors.append("Missing 'workspace' (Bitbucket workspace slug)")
    if not payload.get("repo"):
        errors.append("Missing 'repo' (Bitbucket repository slug)")

    if action_type in ("comment_pr", "approve_pr", "decline_pr", "merge_pr"):
        if not payload.get("pr_id"):
            errors.append("Missing 'pr_id' (pull request ID)")

    if action_type == "comment_pr":
        if not payload.get("comment"):
            errors.append("Missing 'comment' body")

    if action_type == "create_pr":
        if not payload.get("title"):
            errors.append("Missing PR 'title'")
        if not payload.get("source_branch"):
            errors.append("Missing 'source_branch'")
        if not payload.get("dest_branch"):
            errors.append("Missing 'dest_branch'")

    return errors
