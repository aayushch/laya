"""Slack-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive Slack identifiers from the event.

    - ``channel`` and ``thread_ts`` come from content_metadata.
    - For ``react`` actions, ``timestamp`` comes from the event_id
      (the event_id is ``evt_slack_{message_ts}``).
    """
    ids: dict = {}

    meta = content_metadata or {}
    channel = meta.get("slack_channel")
    if channel:
        ids["channel"] = channel

    thread_ts = meta.get("slack_thread_ts")
    if thread_ts:
        ids["thread_ts"] = thread_ts

    if action_type == "react" and event_id and event_id.startswith("evt_slack_"):
        ids["timestamp"] = event_id[len("evt_slack_"):]

    return ids


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Slack executor payload fields."""
    p = dict(payload)

    # Normalize message field
    if "message" not in p and "text" not in p:
        p["message"] = (
            p.pop("body", None)
            or p.pop("content", None)
            or p.pop("raw", None)
            or ""
        )

    # Strip # prefix from channel names (Slack API expects just the name or ID)
    if p.get("channel") and p["channel"].startswith("#"):
        p["channel"] = p["channel"][1:]

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if not payload.get("channel"):
        errors.append("Missing 'channel' (channel name or ID)")

    if action_type in ("send_message", "reply_thread"):
        if not (payload.get("message") or payload.get("text")):
            errors.append("Missing 'message' text")

    if action_type == "reply_thread":
        if not payload.get("thread_ts"):
            errors.append("Missing 'thread_ts' for thread reply")

    if action_type == "react":
        if not payload.get("emoji"):
            errors.append("Missing 'emoji' name")
        if not payload.get("timestamp"):
            errors.append("Missing 'timestamp' of message to react to")

    return errors
