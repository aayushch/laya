"""Calendar-specific payload normalization and validation (Google + Outlook)."""

from __future__ import annotations


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize calendar executor payload fields."""
    p = dict(payload)

    # Normalize title
    if "title" not in p:
        p["title"] = p.pop("summary", None) or p.pop("name", None) or ""

    # Normalize datetime fields
    if "start" not in p:
        p["start"] = p.pop("start_time", None) or p.pop("startTime", None) or ""
    if "end" not in p:
        p["end"] = p.pop("end_time", None) or p.pop("endTime", None) or ""

    return p


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors."""
    errors = []

    if action_type == "create_event":
        if not payload.get("title"):
            errors.append("Missing event 'title'")
        if not payload.get("start"):
            errors.append("Missing 'start' datetime")
        if not payload.get("end"):
            errors.append("Missing 'end' datetime")

    if action_type in ("update_event", "delete_event"):
        if not payload.get("event_id"):
            errors.append("Missing 'event_id'")

    return errors
