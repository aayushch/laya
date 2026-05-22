# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Calendar-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive calendar identifiers from the event.

    ``create_event`` makes a new event and needs no identifier.  Future
    ``update_event``/``delete_event`` actions (not yet in any executor
    workflow) would take the event id from the event_id prefix.
    """
    return {}


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
