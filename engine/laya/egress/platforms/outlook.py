"""Outlook-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive Outlook identifiers from the event.

    Mirror of ``gmail.identifiers_from_event``: message id from event_id,
    reply-to from ``actor_email`` (skipping self), conversation_id from
    metadata.
    """
    ids: dict = {}

    if event_id and event_id.startswith("evt_outlook_"):
        ids["outlook_id"] = event_id[len("evt_outlook_"):]

    conv = (content_metadata or {}).get("outlook_conversation_id")
    if conv:
        ids["conversation_id"] = conv

    # For replies, default "to" to the original sender.  Exclude "forward" —
    # forward recipients are chosen by the user/LLM, not the event's sender.
    if action_type in ("send_email", "reply"):
        actor_email = (event_row or {}).get("actor_email")
        if actor_email:
            if not self_emails or actor_email.lower() not in self_emails:
                ids["to"] = actor_email

    return ids


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Outlook executor payload fields."""
    p = dict(payload)

    # LLMs sometimes emit the generic "message_id" instead of "outlook_id".
    if action_type in ("archive", "mark_read") and not p.get("outlook_id"):
        msg_id = p.pop("message_id", None)
        if msg_id:
            p["outlook_id"] = msg_id

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
