# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Outlook-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform
from laya.egress.platforms.gmail import EMAIL_DRAFT_SCHEMA


class OutlookPlatform(Platform):
    name = "outlook"
    platform_hint = "a professional email"
    chapter_default = "Email"
    draft_schema = EMAIL_DRAFT_SCHEMA  # shares gmail's email draft schema
    source_ref_config = {"use_title": True, "url_template": "https://outlook.office365.com/mail/inbox/id/{id}"}
    compose_guidance = (
        "You are composing an EMAIL. Field requirements:\n"
        "- 'to': MUST be a valid email address (user@domain.com). Never put a name or handle here.\n"
        "- 'cc': MUST be valid email addresses (comma-separated). Never put names or handles here.\n"
        "- 'subject': concise email subject line.\n"
        "- 'body': email body with greeting and signature."
    )
    polish_guidance = (
        "Email — keep any existing greeting and sign-off; use paragraph breaks; professional but warm."
    )

    capabilities = [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["conversation_id", "cc", "bcc"],
            content_fields=["subject", "body"],
            optional_content_fields=["cc", "bcc"],
            description="Send a new email or reply to a thread.",
            summary_template="Send email to {to} with subject '{subject}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="archive",
            label="Archive Email",
            requires_fields=["outlook_id"],
            description="Move email to archive folder.",
            confirmation_required=False,
            summary_template="Archive email (move to archive folder)",
            impact="low",
        ),
        EgressCapability(
            action_type="mark_read",
            label="Mark as Read",
            requires_fields=["outlook_id"],
            description="Mark email as read.",
            confirmation_required=False,
            summary_template="Mark email as read",
            impact="low",
        ),
        EgressCapability(
            action_type="open_url",
            label="Open Link",
            requires_fields=["url"],
            content_fields=["url"],
            description="Open a URL from the email in the user's browser (e.g., unsubscribe link, confirmation link, approval link).",
            confirmation_required=False,
            summary_template="Open {url}",
            impact="low",
        ),
    ]

    def identifiers_from_event(
        self,
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

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
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
                or p.pop("raw", None)
                or ""
            )

        # Reply threading
        if action_type == "send_email" and p.get("conversation_id"):
            subject = p.get("subject", "")
            if subject and not subject.lower().startswith("re:"):
                p["subject"] = f"Re: {subject}"

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors."""
        errors = []

        if action_type == "send_email":
            if not payload.get("to"):
                errors.append("Missing 'to' recipient address")
            if not payload.get("body"):
                errors.append("Missing email 'body'")

        return errors


PLATFORM = OutlookPlatform()
