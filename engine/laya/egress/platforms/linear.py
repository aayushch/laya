# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Linear-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

import re

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform

_EVENT_ID_RE = re.compile(r"^evt_linear_(?P<id>[^_]+)_\d+$")


class LinearPlatform(Platform):
    name = "linear"
    terminal_event_types = frozenset({"issue_resolved"})
    platform_hint = "a Linear issue or comment"
    source_ref_config = {"default_format": "{id}"}
    compose_guidance = (
        "You are composing a LINEAR ISSUE or COMMENT. Field requirements:\n"
        "- 'team_id': Linear team identifier.\n"
        "- 'title': concise one-line issue title.\n"
        "- 'body': full issue/comment body text."
    )
    polish_guidance = (
        "Linear comment — technical tone, direct, concise; preserve any @mentions and issue IDs."
    )

    capabilities = [
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["team_id", "title"],
            optional_fields=["description", "priority", "assignee_id", "state_id"],
            content_fields=["title"],
            optional_content_fields=["description", "priority", "assignee_id", "state_id"],
            description="Create a new Linear issue.",
            summary_template="Create Linear issue: '{title}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="comment",
            label="Comment",
            requires_fields=["issue_id", "body"],
            content_fields=["body"],
            description="Comment on a Linear issue.",
            summary_template="Comment on Linear issue {issue_id}",
            impact="medium",
        ),
        EgressCapability(
            action_type="update_status",
            label="Update Status",
            requires_fields=["issue_id", "state_id"],
            content_fields=["state_id"],
            description="Change the status of a Linear issue.",
            summary_template="Update status of Linear issue {issue_id}",
            impact="high",
        ),
        EgressCapability(
            action_type="assign",
            label="Assign",
            requires_fields=["issue_id", "assignee_id"],
            content_fields=["assignee_id"],
            description="Assign a Linear issue to someone.",
            summary_template="Assign Linear issue {issue_id} to {assignee_id}",
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
        """Derive Linear identifiers from the event.

        ``issue_id`` is the Linear UUID encoded in the event_id.  ``team_id``
        comes from ``content_metadata['linear_team_id']`` (emitted by the
        ingestion workflow alongside the team key).
        """
        ids: dict = {}
        if event_id:
            m = _EVENT_ID_RE.match(event_id)
            if m:
                ids["issue_id"] = m.group("id")
        team_id = (content_metadata or {}).get("linear_team_id")
        if team_id:
            ids["team_id"] = team_id
        return ids

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Normalize Linear executor payload fields."""
        p = dict(payload)

        # Normalize issue ID
        if "issue_id" not in p:
            p["issue_id"] = p.pop("ticket_id", None) or p.pop("id", None) or ""

        # Create issue: normalize team_id
        if action_type == "create_issue" and "team_id" not in p:
            p["team_id"] = p.pop("project", None) or p.pop("team", None) or ""

        # Normalize body/comment
        if action_type == "comment" and "body" not in p:
            p["body"] = (
                p.pop("comment", None)
                or p.pop("message", None)
                or p.pop("content", None)
                or p.pop("raw", None)
                or ""
            )

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors."""
        errors = []

        if action_type in ("comment", "update_status", "assign"):
            if not payload.get("issue_id"):
                errors.append("Missing 'issue_id'")

        if action_type == "comment":
            if not payload.get("body"):
                errors.append("Missing comment 'body'")

        if action_type == "create_issue":
            if not payload.get("team_id"):
                errors.append("Missing 'team_id'")
            if not payload.get("title"):
                errors.append("Missing issue 'title'")

        if action_type == "update_status":
            if not payload.get("state_id"):
                errors.append("Missing 'state_id'")

        if action_type == "assign":
            if not payload.get("assignee_id"):
                errors.append("Missing 'assignee_id'")

        return errors


PLATFORM = LinearPlatform()
