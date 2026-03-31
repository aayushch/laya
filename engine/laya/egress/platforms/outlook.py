"""Outlook-specific payload normalization and validation."""

from __future__ import annotations


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Outlook executor payload fields."""
    p = dict(payload)

    # Ensure "body" exists
    if "body" not in p:
        p["body"] = (
            p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or ""
        )

    # Reply threading
    if action_type == "send_email" and p.get("conversation_id"):
        subject = p.get("subject", "")
        if subject and not subject.lower().startswith("re:"):
            p["subject"] = f"Re: {subject}"

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if action_type == "send_email":
        if not payload.get("to"):
            errors.append("Missing 'to' recipient address")
        if not payload.get("body"):
            errors.append("Missing email 'body'")

    return errors
