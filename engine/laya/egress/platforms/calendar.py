# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Calendar-specific payload normalization, validation, and event-derived identifiers.

Google Calendar (``calendar``) and Outlook Calendar (``outlook_calendar``) share
all behavior but differ in their declarative data (labels/descriptions), so the
behavior lives in ``CalendarBehavior`` and each platform is a thin leaf subclass.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform


class CalendarBehavior(Platform):
    """Shared calendar behavior. Abstract — it defines neither ``name`` nor
    ``capabilities``, so it is never instantiated directly; the two leaves below
    supply those."""

    body_field = "description"
    chapter_default = "Meeting"

    def identifiers_from_event(
        self,
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

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
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

        # Localize naive datetimes to RFC3339 with offset when timezone is provided.
        # Without this, "2026-06-04T09:00" is ambiguous — the Calendar API may
        # interpret it as UTC, shifting the event by the user's UTC offset.
        tz_name = p.get("timezone")
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
                for field in ("start", "end"):
                    raw = p.get(field)
                    if raw and "+" not in raw and "Z" not in raw:
                        dt = datetime.fromisoformat(raw)
                        p[field] = dt.replace(tzinfo=tz).isoformat()
            except (ValueError, KeyError):
                pass

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
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


class GoogleCalendarPlatform(CalendarBehavior):
    name = "calendar"
    platform_hint = "a calendar event"
    source_ref_config = {"use_title": True}
    compose_guidance = (
        "You are creating a CALENDAR EVENT. Field requirements:\n"
        "- 'title': event title.\n"
        "- 'start': start date/time.\n"
        "- 'end': end date/time.\n"
        "- 'description': event description (optional).\n"
        "- 'attendees': attendee email addresses (optional)."
    )

    capabilities = [
        EgressCapability(
            action_type="create_event",
            label="Create Event",
            requires_fields=["title", "start", "end"],
            optional_fields=["description", "attendees", "location"],
            content_fields=["title", "start", "end"],
            optional_content_fields=["description", "attendees", "location"],
            description="Create a calendar event.",
            summary_template="Create calendar event: '{title}'",
            impact="medium",
        ),
    ]


class OutlookCalendarPlatform(CalendarBehavior):
    name = "outlook_calendar"
    platform_hint = "an Outlook calendar event"
    compose_guidance = (
        "You are creating an OUTLOOK CALENDAR EVENT. Field requirements:\n"
        "- 'title': event title.\n"
        "- 'start': start date/time.\n"
        "- 'end': end date/time.\n"
        "- 'description': event description (optional).\n"
        "- 'attendees': attendee email addresses (optional)."
    )

    capabilities = [
        EgressCapability(
            action_type="create_event",
            label="Create Event",
            requires_fields=["title", "start", "end"],
            optional_fields=["description", "attendees", "location"],
            content_fields=["title", "start", "end"],
            optional_content_fields=["description", "attendees", "location"],
            description="Create an Outlook calendar event.",
            summary_template="Create Outlook calendar event: '{title}'",
            impact="medium",
        ),
    ]


GOOGLE_CALENDAR = GoogleCalendarPlatform()
OUTLOOK_CALENDAR = OutlookCalendarPlatform()

# Backward-compatible alias: behavior is identical across the two leaves, so the
# generic ``PLATFORM`` name (used by tests and the legacy "calendar" key) points
# at the Google leaf.
PLATFORM = GOOGLE_CALENDAR
