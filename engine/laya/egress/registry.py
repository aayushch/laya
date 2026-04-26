"""Platform capability registry — single source of truth for what each platform supports.

Each entry here mirrors the action_types handled by the corresponding n8n
executor workflow at ``n8n/workflows/<platform>-executor.json``.  Keep these
two in lock-step: adding a new action to an executor must also add it here
(and vice versa).  The parity test ``tests/test_egress_registry_parity.py``
enforces this at CI time — it parses each executor Switch node and asserts
the action_types match the registry.

Exceptions:
- ``smtp`` is backed by ``SmtpBackend`` (not n8n) and therefore has no
  corresponding executor workflow. The parity test skips it.
"""

from __future__ import annotations

from laya.egress.models import EgressCapability

# ---------------------------------------------------------------------------
# Platform capability definitions
# ---------------------------------------------------------------------------

_CAPABILITIES: dict[str, list[EgressCapability]] = {
    # Keep in sync with n8n/workflows/gmail-executor.json
    "gmail": [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["thread_id", "cc", "bcc"],
            content_fields=["subject", "body"],
            optional_content_fields=["cc", "bcc"],
            description="Send a new email or reply to a thread.",
            summary_template="Send email to {to} with subject '{subject}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="forward",
            label="Forward Email",
            requires_fields=["to", "body"],
            optional_fields=["subject", "gmail_id"],
            content_fields=["to", "body"],
            optional_content_fields=["subject", "cc", "bcc"],
            description="Forward an email to another recipient.",
            summary_template="Forward email to {to}",
            impact="medium",
        ),
        EgressCapability(
            action_type="archive",
            label="Archive Email",
            requires_fields=["gmail_id"],
            description="Remove email from inbox (archive).",
            confirmation_required=False,
            summary_template="Archive email (remove from inbox)",
            impact="low",
        ),
        EgressCapability(
            action_type="star",
            label="Star Email",
            requires_fields=["gmail_id"],
            description="Star/flag an email.",
            confirmation_required=False,
            summary_template="Star email",
            impact="low",
        ),
        EgressCapability(
            action_type="mark_read",
            label="Mark as Read",
            requires_fields=["gmail_id"],
            description="Mark email as read.",
            confirmation_required=False,
            summary_template="Mark email as read",
            impact="low",
        ),
    ],
    # Keep in sync with n8n/workflows/outlook-email-executor.json
    "outlook": [
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
    ],
    # SmtpBackend-only — no n8n executor.  No event context → all fields
    # must be caller/LLM-provided, so content_fields mirror requires_fields.
    "smtp": [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["in_reply_to", "references", "cc", "bcc"],
            content_fields=["to", "subject", "body"],
            optional_content_fields=["cc", "bcc"],
            description="Send email via SMTP (generic email provider).",
            summary_template="Send email to {to} with subject '{subject}'",
            impact="medium",
        ),
    ],
    # Keep in sync with n8n/workflows/jira-executor.json
    "jira": [
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
    ],
    # Keep in sync with n8n/workflows/notion-executor.json
    "notion": [
        EgressCapability(
            action_type="create_page",
            label="Create Page",
            requires_fields=["parent_id", "title"],
            optional_fields=["parent_type", "properties", "children"],
            content_fields=["title"],
            optional_content_fields=["properties", "children"],
            description="Create a new Notion page inside a database or parent page.",
            summary_template="Create Notion page: '{title}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="append_block",
            label="Append Block",
            requires_fields=["page_id", "text"],
            optional_fields=["block_type"],
            content_fields=["text"],
            optional_content_fields=["block_type"],
            description="Append a text block to a Notion page.",
            summary_template="Append block to page {page_id}",
            impact="medium",
        ),
        EgressCapability(
            action_type="update_page_property",
            label="Update Property",
            requires_fields=["page_id", "property_name", "property_value"],
            optional_fields=["property_type"],
            content_fields=["property_value"],
            optional_content_fields=["property_type"],
            description="Update a single property on a Notion page (status, tags, etc.).",
            summary_template="Update '{property_name}' on page {page_id}",
            impact="medium",
        ),
        EgressCapability(
            action_type="archive_page",
            label="Archive Page",
            requires_fields=["page_id"],
            description="Archive a Notion page (soft delete).",
            summary_template="Archive page {page_id}",
            warnings=["This will archive the Notion page. It can be restored from Notion's trash."],
            impact="medium",
        ),
        EgressCapability(
            action_type="add_comment",
            label="Add Comment",
            requires_fields=["page_id", "comment"],
            content_fields=["comment"],
            description="Add a comment to a Notion page.",
            summary_template="Comment on page {page_id}",
            impact="medium",
        ),
    ],
    # Keep in sync with n8n/workflows/github-executor.json
    "github": [
        EgressCapability(
            action_type="comment",
            label="Comment on Issue",
            requires_fields=["owner", "repo", "issue_number", "comment"],
            content_fields=["comment"],
            description="Post a comment on a GitHub issue or PR.",
            summary_template="Comment on {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="close_issue",
            label="Close Issue",
            requires_fields=["owner", "repo", "issue_number"],
            optional_fields=["comment"],
            optional_content_fields=["comment"],
            description="Close a GitHub issue.",
            summary_template="Close {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["owner", "repo", "title"],
            optional_fields=["body", "labels", "assignees"],
            content_fields=["title"],
            optional_content_fields=["body", "labels", "assignees"],
            description="Create a new GitHub issue.",
            summary_template="Create issue in {owner}/{repo}: '{title}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["comment"],
            optional_content_fields=["comment"],
            description="Approve a pull request.",
            summary_template="Approve PR {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="request_changes",
            label="Request Changes",
            requires_fields=["owner", "repo", "pr_number", "comment"],
            content_fields=["comment"],
            description="Request changes on a pull request.",
            summary_template="Request changes on PR {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["merge_method", "commit_title"],
            optional_content_fields=["merge_method", "commit_title"],
            description="Merge a pull request.",
            summary_template="Merge PR {gh_ref}",
            warnings=["This will merge the pull request. This action cannot be undone."],
            impact="high",
        ),
    ],
    # Keep in sync with n8n/workflows/bitbucket-executor.json
    "bitbucket": [
        EgressCapability(
            action_type="comment_pr",
            label="Comment on PR",
            requires_fields=["workspace", "repo", "pr_id", "comment"],
            content_fields=["comment"],
            description="Post a comment on a Bitbucket pull request.",
            summary_template="Comment on {bb_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Approve a Bitbucket pull request.",
            summary_template="Approve {bb_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="decline_pr",
            label="Decline PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Decline a Bitbucket pull request.",
            summary_template="Decline {bb_ref}",
            warnings=["This will decline the pull request."],
            impact="high",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["workspace", "repo", "pr_id"],
            optional_fields=["merge_strategy", "close_source_branch"],
            optional_content_fields=["merge_strategy", "close_source_branch"],
            description="Merge a Bitbucket pull request.",
            summary_template="Merge {bb_ref}",
            warnings=["This will merge the pull request. This action cannot be undone."],
            impact="high",
        ),
    ],
    # Keep in sync with n8n/workflows/slack-executor.json
    "slack": [
        EgressCapability(
            action_type="send_message",
            label="Send Message",
            requires_fields=["channel", "message"],
            content_fields=["message"],
            description="Send a message to a Slack channel.",
            summary_template="Send message to {channel}",
            impact="medium",
        ),
        EgressCapability(
            action_type="reply_thread",
            label="Reply to Thread",
            requires_fields=["channel", "thread_ts", "message"],
            content_fields=["message"],
            description="Reply to a Slack thread.",
            summary_template="Reply in thread in {channel}",
            impact="medium",
        ),
        EgressCapability(
            action_type="react",
            label="React",
            requires_fields=["channel", "timestamp", "emoji"],
            content_fields=["emoji"],
            description="Add an emoji reaction to a message.",
            confirmation_required=False,
            summary_template="React with :{emoji}: in {channel}",
            impact="low",
        ),
    ],
    # Keep in sync with n8n/workflows/linear-executor.json
    "linear": [
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
    ],
    # Keep in sync with n8n/workflows/google-calendar-executor.json
    # ("calendar" is the internal platform name for Google Calendar.)
    "calendar": [
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
    ],
    # Keep in sync with n8n/workflows/outlook-calendar-executor.json
    "outlook_calendar": [
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
    ],
}


_COMPOSE_GUIDANCE: dict[str, str] = {
    "gmail": (
        "You are composing an EMAIL. Field requirements:\n"
        "- 'to': MUST be a valid email address (user@domain.com). Never put a name or handle here.\n"
        "- 'cc': MUST be valid email addresses (comma-separated). Never put names or handles here.\n"
        "- 'subject': concise email subject line.\n"
        "- 'body': email body with greeting and signature."
    ),
    "outlook": (
        "You are composing an EMAIL. Field requirements:\n"
        "- 'to': MUST be a valid email address (user@domain.com). Never put a name or handle here.\n"
        "- 'cc': MUST be valid email addresses (comma-separated). Never put names or handles here.\n"
        "- 'subject': concise email subject line.\n"
        "- 'body': email body with greeting and signature."
    ),
    "slack": (
        "You are composing a SLACK MESSAGE. Field requirements:\n"
        "- 'channel': Slack channel name or ID (e.g. '#general', 'C01234'). Not a person's name.\n"
        "- 'message': message text (supports mrkdwn formatting)."
    ),
    "jira": (
        "You are composing a JIRA ISSUE or COMMENT. Field requirements:\n"
        "- 'project': uppercase Jira project key (e.g. 'PROJ', 'ENG'). Not a full project name.\n"
        "- 'summary': concise one-line issue title.\n"
        "- 'description': full issue/comment body text."
    ),
    "linear": (
        "You are composing a LINEAR ISSUE or COMMENT. Field requirements:\n"
        "- 'team_id': Linear team identifier.\n"
        "- 'title': concise one-line issue title.\n"
        "- 'body': full issue/comment body text."
    ),
    "github": (
        "You are composing a GITHUB ISSUE, PR, or COMMENT. Field requirements:\n"
        "- 'repo': repository in owner/repo format (e.g. 'acme/backend').\n"
        "- 'title': concise one-line issue/PR title.\n"
        "- 'body': full body text (supports markdown)."
    ),
    "bitbucket": (
        "You are composing a BITBUCKET PR or COMMENT. Field requirements:\n"
        "- 'repo': repository in workspace/repo format.\n"
        "- 'title': concise one-line PR title.\n"
        "- 'body': full body text (supports markdown)."
    ),
    "notion": (
        "You are composing a NOTION PAGE or COMMENT. Field requirements:\n"
        "- 'title': page title.\n"
        "- 'body': page or comment body text."
    ),
    "calendar": (
        "You are creating a CALENDAR EVENT. Field requirements:\n"
        "- 'title': event title.\n"
        "- 'start': start date/time.\n"
        "- 'end': end date/time.\n"
        "- 'description': event description (optional).\n"
        "- 'attendees': attendee email addresses (optional)."
    ),
    "outlook_calendar": (
        "You are creating an OUTLOOK CALENDAR EVENT. Field requirements:\n"
        "- 'title': event title.\n"
        "- 'start': start date/time.\n"
        "- 'end': end date/time.\n"
        "- 'description': event description (optional).\n"
        "- 'attendees': attendee email addresses (optional)."
    ),
}

_PLATFORM_HINTS: dict[str, str] = {
    "gmail": "a professional email",
    "outlook": "a professional email",
    "smtp": "a professional email",
    "slack": "a Slack message",
    "jira": "a Jira issue or comment",
    "linear": "a Linear issue or comment",
    "github": "a GitHub issue, PR, or comment",
    "bitbucket": "a Bitbucket PR or comment",
    "notion": "a Notion page or comment",
    "calendar": "a calendar event",
    "outlook_calendar": "an Outlook calendar event",
}


_DEFAULT_DRAFT_SCHEMA: dict = {
    "name": "draft_output",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": "The drafted message body text.",
            },
        },
        "required": ["body"],
        "additionalProperties": False,
    },
}

_DRAFT_SCHEMAS: dict[str, dict] = {
    "gmail": {
        "name": "email_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Recipient email address in user@domain.com format. "
                        "MUST be a valid email address — never a plain name or handle. "
                        "Call find_contact to resolve names/handles to email addresses. "
                        "Empty string if unknown."
                    ),
                },
                "cc": {
                    "type": "string",
                    "description": (
                        "CC email addresses in user@domain.com format (comma-separated if multiple). "
                        "MUST be valid email addresses — never plain names or handles. "
                        "Call find_contact to resolve names/handles to email addresses. "
                        "Empty string if none."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line. Empty string if already provided in context.",
                },
                "body": {
                    "type": "string",
                    "description": "The email body text, ready to send. Include greeting and signature.",
                },
            },
            "required": ["to", "cc", "subject", "body"],
            "additionalProperties": False,
        },
    },
    "slack": {
        "name": "slack_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": (
                        "Slack channel name or ID (e.g. '#general', 'C01234'). "
                        "Empty string if unknown."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The Slack message text (supports mrkdwn formatting).",
                },
            },
            "required": ["channel", "message"],
            "additionalProperties": False,
        },
    },
    "jira": {
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
    },
    "github": {
        "name": "github_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": (
                        "Repository in owner/repo format (e.g. 'acme/backend'). "
                        "Empty string if unknown."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Issue or PR title — a concise one-line description. Empty string if not applicable.",
                },
                "body": {
                    "type": "string",
                    "description": "Issue/PR/comment body text (supports markdown).",
                },
            },
            "required": ["repo", "title", "body"],
            "additionalProperties": False,
        },
    },
}
# Platforms that share a schema with another
_DRAFT_SCHEMAS["outlook"] = _DRAFT_SCHEMAS["gmail"]
_DRAFT_SCHEMAS["smtp"] = _DRAFT_SCHEMAS["gmail"]
_DRAFT_SCHEMAS["bitbucket"] = _DRAFT_SCHEMAS["github"]


def get_draft_schema(platform: str) -> dict:
    """Return the structured output JSON schema for composing on a platform."""
    return _DRAFT_SCHEMAS.get(platform, _DEFAULT_DRAFT_SCHEMA)


_BODY_FIELD: dict[str, str] = {
    "gmail": "body",
    "outlook": "body",
    "smtp": "body",
    "slack": "message",
    "jira": "description",
    "linear": "body",
    "github": "body",
    "bitbucket": "body",
    "notion": "body",
    "calendar": "description",
    "outlook_calendar": "description",
}


def get_body_field(platform: str) -> str:
    """Return the name of the primary text/body field for a platform."""
    return _BODY_FIELD.get(platform, "body")


def get_compose_guidance(platform: str) -> str:
    """Return LLM compose guidance for a platform, or empty string if unknown."""
    return _COMPOSE_GUIDANCE.get(platform, "")


def get_platform_hint(platform: str) -> str:
    """Return a short human-readable description like 'a professional email'."""
    return _PLATFORM_HINTS.get(platform, f"a {platform} message")


def get_capabilities(platform: str) -> list[EgressCapability]:
    """Return the list of capabilities for a platform."""
    return _CAPABILITIES.get(platform, [])


def get_all_platforms() -> list[str]:
    """Return all registered platform identifiers."""
    return list(_CAPABILITIES.keys())


def supports_action(platform: str, action_type: str) -> bool:
    """Check if a platform supports a specific action type."""
    return any(c.action_type == action_type for c in _CAPABILITIES.get(platform, []))


def get_capability(platform: str, action_type: str) -> EgressCapability | None:
    """Get a specific capability by platform and action type."""
    for cap in _CAPABILITIES.get(platform, []):
        if cap.action_type == action_type:
            return cap
    return None
