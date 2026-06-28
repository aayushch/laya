# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Jira-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform

JIRA_DRAFT_SCHEMA: dict = {
    "name": "jira_draft",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": (
                    "Jira project key (e.g. 'PROJ', 'ENG'). "
                    "Must be an uppercase project key, not a full name. "
                    "Empty string if unknown."
                ),
            },
            "summary": {
                "type": "string",
                "description": "Issue summary/title — a concise one-line description. Empty string if not applicable.",
            },
            "description": {
                "type": "string",
                "description": "Issue or comment body text with full details.",
            },
        },
        "required": ["project", "summary", "description"],
        "additionalProperties": False,
    },
}


class JiraPlatform(Platform):
    name = "jira"
    terminal_event_types = frozenset({"issue_resolved"})
    platform_hint = "a Jira issue or comment"
    body_field = "description"
    draft_schema = JIRA_DRAFT_SCHEMA
    source_ref_config = {"default_format": "{id}"}
    compose_guidance = (
        "You are composing a JIRA ISSUE or COMMENT. Field requirements:\n"
        "- 'project': uppercase Jira project key (e.g. 'PROJ', 'ENG'). Not a full project name.\n"
        "- 'summary': concise one-line issue title.\n"
        "- 'description': full issue/comment body text."
    )
    polish_guidance = (
        "Jira comment — technical tone, direct, concise; preserve any @mentions and ticket IDs."
    )

    capabilities = [
        EgressCapability(
            action_type="comment",
            label="Post Comment",
            requires_fields=["issue_key", "comment"],
            optional_fields=["visibility"],
            content_fields=["comment"],
            optional_content_fields=["visibility"],
            description="Add a comment to a Jira issue.",
            summary_template="Post comment on {issue_key}",
            impact="medium",
        ),
        EgressCapability(
            action_type="transition",
            label="Change Status",
            requires_fields=["issue_key", "target_status"],
            optional_fields=["comment"],
            content_fields=["target_status"],
            optional_content_fields=["comment"],
            description="Transition a Jira issue to a new status.",
            summary_template="Transition {issue_key} to '{target_status}'",
            impact="high",
        ),
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["project", "summary"],
            optional_fields=["description", "type", "priority", "assignee", "labels"],
            content_fields=["summary"],
            optional_content_fields=["description", "type", "priority", "assignee", "labels"],
            description="Create a new Jira issue.",
            summary_template="Create issue in {project}: '{summary}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="assign",
            label="Assign Issue",
            requires_fields=["issue_key", "assignee"],
            content_fields=["assignee"],
            description="Assign a Jira issue to someone.",
            summary_template="Assign {issue_key} to {assignee}",
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
        """Derive Jira identifiers from the source event.

        ``subject_id`` holds the canonical issue key (e.g. ``PROJ-123``) that
        every Jira action except ``create_issue`` needs.  ``jira_project`` in
        the metadata covers the create path.
        """
        ids: dict = {}
        subject_id = (event_row or {}).get("subject_id")
        if subject_id:
            ids["issue_key"] = subject_id
        project = (content_metadata or {}).get("jira_project")
        if project:
            ids["project"] = project
        return ids

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Normalize Jira executor payload fields."""
        p = dict(payload)

        # Normalize issue key field name
        if "issue_key" not in p:
            p["issue_key"] = (
                p.pop("issueKey", None)
                or p.pop("key", None)
                or p.pop("ticket_id", None)
                or ""
            )

        # Normalize comment field
        if action_type == "comment" and "comment" not in p:
            p["comment"] = (
                p.pop("body", None)
                or p.pop("message", None)
                or p.pop("content", None)
                or p.pop("text", None)
                or p.pop("raw", None)
                or ""
            )

        # Normalize create_issue fields
        if action_type == "create_issue":
            if "summary" not in p:
                p["summary"] = p.pop("title", None) or ""
            if "type" not in p:
                p["type"] = p.pop("issueType", None) or p.pop("issue_type", None) or "Task"

        # Normalize assignee aliases (LLM may emit various field names)
        if action_type in ("assign", "create_issue") and not p.get("assignee"):
            p["assignee"] = (
                p.pop("account_id", None)
                or p.pop("accountId", None)
                or p.pop("user", None)
                or p.pop("user_email", None)
                or ""
            )

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors."""
        errors = []

        if action_type in ("comment", "transition", "assign", "update_fields"):
            if not payload.get("issue_key"):
                errors.append("Missing 'issue_key' (e.g., 'PROJ-123')")

        if action_type == "comment":
            if not payload.get("comment"):
                errors.append("Missing 'comment' body")

        if action_type == "transition":
            if not payload.get("target_status"):
                errors.append("Missing 'target_status' (e.g., 'Done', 'In Progress')")

        if action_type == "create_issue":
            if not payload.get("project"):
                errors.append("Missing 'project' key (e.g., 'PROJ')")
            if not payload.get("summary"):
                errors.append("Missing issue 'summary' / title")

        if action_type == "assign":
            if not payload.get("assignee"):
                errors.append("Missing 'assignee'")

        return errors


PLATFORM = JiraPlatform()
