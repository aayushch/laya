"""Gmail-specific payload normalization and validation."""

from __future__ import annotations


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Gmail executor payload fields."""
    p = dict(payload)

    # Ensure "body" exists — LLMs use various key names
    if "body" not in p:
        p["body"] = (
            p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or p.pop("reply_body", None)
            or p.pop("email_body", None)
            or p.pop("reply", None)
            or ""
        )

    # Ensure subject has Re: prefix for replies
    if action_type == "send_email" and p.get("thread_id"):
        subject = p.get("subject", "")
        if subject and not subject.lower().startswith("re:"):
            p["subject"] = f"Re: {subject}"

    # Forward: ensure Fwd: prefix
    if action_type == "forward":
        subject = p.get("subject", "")
        if subject and not subject.lower().startswith("fwd:"):
            p["subject"] = f"Fwd: {subject}"

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors (empty if valid)."""
    errors = []

    if action_type == "send_email":
        if not payload.get("to"):
            errors.append("Missing 'to' recipient address")
        if not payload.get("body"):
            errors.append("Missing email 'body'")

    elif action_type == "forward":
        if not payload.get("to"):
            errors.append("Missing 'to' forward recipient")

    elif action_type in ("archive", "star", "unstar", "mark_read"):
        if not payload.get("gmail_id"):
            errors.append(f"Missing 'gmail_id' for {action_type}")

    return errors
