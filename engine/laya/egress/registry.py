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


# ---------------------------------------------------------------------------
# Platform keywords — used to detect platform from n8n workflow names
# ---------------------------------------------------------------------------

_PLATFORM_KEYWORDS: dict[str, str] = {
    "github": "github",
    "gmail": "gmail",
    "jira": "jira",
    "slack": "slack",
    "bitbucket": "bitbucket",
    "calendar": "calendar",
    "outlook": "outlook",
    "linear": "linear",
    "notion": "notion",
    "discord": "discord",
    "gitlab": "gitlab",
}


def get_platform_keywords() -> dict[str, str]:
    """Return keyword→platform mapping for workflow name parsing."""
    return dict(_PLATFORM_KEYWORDS)


# ---------------------------------------------------------------------------
# Chapter defaults — default trace chapter label per platform
# ---------------------------------------------------------------------------

_PLATFORM_CHAPTER_DEFAULTS: dict[str, str] = {
    "jira": "Update",
    "github": "Code",
    "bitbucket": "Code",
    "slack": "Discussion",
    "gmail": "Email",
    "outlook": "Email",
    "calendar": "Meeting",
    "outlook_calendar": "Meeting",
    "linear": "Update",
    "notion": "Note",
}


def get_chapter_default(platform: str) -> str:
    """Return the default trace chapter label for a platform."""
    return _PLATFORM_CHAPTER_DEFAULTS.get(platform, "Update")


# ---------------------------------------------------------------------------
# Polish guidance — LLM tone/style guidance for draft polishing
# ---------------------------------------------------------------------------

_POLISH_GUIDANCE: dict[str, str] = {
    "gmail": "Email — keep any existing greeting and sign-off; use paragraph breaks; professional but warm.",
    "outlook": "Email — keep any existing greeting and sign-off; use paragraph breaks; professional but warm.",
    "jira": "Jira comment — technical tone, direct, concise; preserve any @mentions and ticket IDs.",
    "linear": "Linear comment — technical tone, direct, concise; preserve any @mentions and issue IDs.",
    "github": "GitHub comment — technical tone, direct; preserve code blocks, @mentions, and issue/PR references.",
    "bitbucket": "Bitbucket comment — technical tone, direct; preserve code blocks, @mentions, and PR references.",
    "slack": "Slack message — conversational and concise; preserve any @mentions, channel refs, and links.",
    "notion": "Notion page — clear and structured; preserve any @mentions and links.",
}


def get_polish_guidance(platform: str) -> str:
    """Return LLM polish tone guidance for a platform."""
    return _POLISH_GUIDANCE.get((platform or "").lower(), "General professional correspondence.")


# ---------------------------------------------------------------------------
# Legacy platform map — pre-Spaces hardcoded connection_id values
# ---------------------------------------------------------------------------

_LEGACY_PLATFORM_MAP: dict[str, str] = {
    "gmail_main": "gmail",
    "jira_main": "jira",
    "slack_main": "slack",
    "calendar_main": "calendar",
    "bb_main": "bitbucket",
    "outlook_main": "outlook",
    "outlook_calendar_main": "outlook_calendar",
}


def resolve_legacy_platform(connection_id: str) -> str | None:
    """Map a legacy hardcoded connection_id to its platform, or None."""
    return _LEGACY_PLATFORM_MAP.get(connection_id)


# ---------------------------------------------------------------------------
# Source reference formatting — how to display subject IDs per platform
# ---------------------------------------------------------------------------

_SOURCE_REF_CONFIG: dict[str, dict] = {
    "github": {"pr_format": "PR #{id}", "default_format": "#{id}"},
    "jira": {"default_format": "{id}"},
    "gmail": {"use_title": True},
    "slack": {"use_title": True},
    "outlook": {"use_title": True, "url_template": "https://outlook.office365.com/mail/inbox/id/{id}"},
    "linear": {"default_format": "{id}"},
    "bitbucket": {"pr_format": "PR #{id}", "default_format": "#{id}"},
    "notion": {"use_title": True},
    "calendar": {"use_title": True},
}


def format_source_ref(
    platform: str,
    subject_id: str,
    subject_type: str | None,
    subject_title: str | None,
    source_url: str | None,
) -> tuple[str | None, str | None]:
    """Format a source reference and URL for display.

    Returns (source_ref, source_url) — either may be None.
    """
    config = _SOURCE_REF_CONFIG.get(platform, {})

    if config.get("use_title"):
        ref = subject_title or subject_id
    elif subject_type == "pull_request" and "pr_format" in config:
        ref = config["pr_format"].replace("{id}", subject_id)
    elif "default_format" in config:
        ref = config["default_format"].replace("{id}", subject_id)
    else:
        ref = subject_id or None

    if not source_url and "url_template" in config and subject_id:
        source_url = config["url_template"].replace("{id}", subject_id)

    return ref, source_url


# ---------------------------------------------------------------------------
# Platform groups — derive from capabilities for tool enum lists
# ---------------------------------------------------------------------------


def get_email_platforms() -> list[str]:
    """Platforms that support send_email action."""
    result = [p for p, caps in _CAPABILITIES.items() if any(c.action_type == "send_email" for c in caps)]
    if "smtp" not in result:
        result.append("smtp")
    return result


def get_ticket_platforms() -> list[str]:
    """Platforms that support comment or create_issue actions."""
    return [
        p for p, caps in _CAPABILITIES.items()
        if any(c.action_type in ("comment", "create_issue") for c in caps)
    ]


def get_transition_platforms() -> list[str]:
    """Platforms that support status transitions."""
    return [
        p for p, caps in _CAPABILITIES.items()
        if any(c.action_type in ("transition", "update_status") for c in caps)
    ]


def get_pr_platforms() -> list[str]:
    """Platforms that support PR actions (approve, merge, etc.)."""
    return [
        p for p, caps in _CAPABILITIES.items()
        if any(c.action_type in ("approve_pr", "merge_pr") for c in caps)
    ]


def get_composable_platforms() -> list[str]:
    """Platforms that can be used with the open_compose tool."""
    return list(_CAPABILITIES.keys())


# ---------------------------------------------------------------------------
# Compose UI — field metadata for dynamic form rendering
# ---------------------------------------------------------------------------

_FIELD_META: dict[str, dict] = {
    # Email
    "to": {"label": "To", "type": "email", "placeholder": "recipient@example.com"},
    "cc": {"label": "CC", "type": "email", "placeholder": "cc@example.com"},
    "bcc": {"label": "BCC", "type": "email", "placeholder": "bcc@example.com"},
    "subject": {"label": "Subject", "type": "text", "placeholder": "Subject"},
    "body": {"label": "Body", "type": "textarea", "placeholder": "Write your message..."},
    # Slack
    "channel": {"label": "Channel", "type": "text", "placeholder": "#general"},
    "message": {"label": "Message", "type": "textarea", "placeholder": "Write your message..."},
    "thread_ts": {"label": "Thread Timestamp", "type": "text", "placeholder": "1234567890.123456"},
    # Jira
    "issue_key": {"label": "Issue Key", "type": "text", "placeholder": "PROJ-123"},
    "project": {"label": "Project", "type": "text", "placeholder": "PROJ"},
    "summary": {"label": "Summary", "type": "text", "placeholder": "Issue summary"},
    "priority": {"label": "Priority", "type": "select", "options": ["Highest", "High", "Medium", "Low", "Lowest"]},
    "type": {"label": "Type", "type": "select", "options": ["Bug", "Task", "Story"]},
    # GitHub / Bitbucket
    "repo": {"label": "Repository", "type": "text", "placeholder": "owner/repo"},
    "issue_number": {"label": "Issue #", "type": "text", "placeholder": "123"},
    "pr_number": {"label": "PR #", "type": "text", "placeholder": "456"},
    "pr_id": {"label": "PR ID", "type": "text", "placeholder": "123"},
    # Linear
    "issue_id": {"label": "Issue ID", "type": "text", "placeholder": "Issue ID"},
    "team_id": {"label": "Team", "type": "text", "placeholder": "Team key or ID"},
    "state_id": {"label": "State", "type": "text", "placeholder": "State ID"},
    "assignee_id": {"label": "Assignee", "type": "text", "placeholder": "User ID"},
    # Notion
    "page_id": {"label": "Page ID", "type": "text", "placeholder": "Notion page ID"},
    "parent_id": {"label": "Parent ID", "type": "text", "placeholder": "Notion parent page/database ID"},
    "property_name": {"label": "Property", "type": "text", "placeholder": "Property name"},
    "property_value": {"label": "Value", "type": "text", "placeholder": "New value"},
    "text": {"label": "Text", "type": "textarea", "placeholder": "Write your text..."},
    # Shared
    "title": {"label": "Title", "type": "text", "placeholder": "Title"},
    "description": {"label": "Description", "type": "textarea", "placeholder": "Describe..."},
    "comment": {"label": "Comment", "type": "textarea", "placeholder": "Write your comment..."},
    "target_status": {"label": "Target Status", "type": "text", "placeholder": "Done"},
    "labels": {"label": "Labels", "type": "text", "placeholder": "bug, enhancement (comma-separated)"},
    "assignee": {"label": "Assignee", "type": "text", "placeholder": "Username or email"},
    "assignees": {"label": "Assignees", "type": "text", "placeholder": "Usernames (comma-separated)"},
    "emoji": {"label": "Emoji", "type": "text", "placeholder": ":thumbsup:"},
    "merge_method": {"label": "Merge Method", "type": "select", "options": ["squash", "merge", "rebase"]},
    "commit_title": {"label": "Commit Title", "type": "text", "placeholder": "Merge commit title"},
    # Calendar
    "start": {"label": "Start", "type": "text", "placeholder": "2026-01-15T09:00"},
    "end": {"label": "End", "type": "text", "placeholder": "2026-01-15T10:00"},
    "location": {"label": "Location", "type": "text", "placeholder": "Room / URL"},
    "attendees": {"label": "Attendees", "type": "text", "placeholder": "email@example.com (comma-separated)"},
}

# Fields that are never shown in the composer — engine-internal IDs or
# fields derived from another visible field (e.g. owner from repo).
_COMPOSE_HIDDEN_FIELDS = {
    "gmail_id",             # internal Gmail message ID
    "outlook_id",           # internal Outlook message ID
    "timestamp",            # Slack reaction timestamp
    "owner",                # derived from repo field (owner/repo format)
    "workspace",            # derived from repo field (workspace/repo format)
    "parent_type",          # Notion internal
    "children",             # Notion internal (JSON block array)
    "properties",           # Notion internal (JSON properties)
    "block_type",           # Notion internal
    "close_source_branch",  # Bitbucket merge option
    "conversation_id",      # Outlook thread ID
    "merge_strategy",       # Bitbucket merge variant (use merge_method)
    "visibility",           # Jira comment visibility (rarely used manually)
}

_NON_COMPOSABLE_ACTIONS = {
    "archive", "star", "mark_read", "react",
    "archive_page",
    "forward",      # needs original email ID from event context
    "decline_pr",   # destructive — not a compose action
}


def get_compose_fields(platform: str, action_type: str) -> list[dict]:
    """Return the ordered field list for a compose action.

    Shows ALL required and optional fields except engine-internal ones
    listed in _COMPOSE_HIDDEN_FIELDS. This ensures the composer has
    every field the user needs to fill for manual composition.
    """
    cap = get_capability(platform, action_type)
    if not cap:
        return []

    seen: set[str] = set()
    fields: list[dict] = []

    def _add(name: str, required: bool) -> None:
        if name in seen or name in _COMPOSE_HIDDEN_FIELDS:
            return
        seen.add(name)
        meta = _FIELD_META.get(name, {
            "label": name.replace("_", " ").title(),
            "type": "text",
            "placeholder": "",
        })
        entry: dict = {
            "name": name,
            "required": required,
            "type": meta["type"],
            "label": meta["label"],
            "placeholder": meta.get("placeholder", ""),
        }
        if "options" in meta:
            entry["options"] = meta["options"]
        fields.append(entry)

    for f in cap.requires_fields:
        _add(f, True)

    for f in cap.optional_fields:
        _add(f, False)

    return fields


def get_compose_platforms_data() -> list[dict]:
    """Assemble the full compose-platforms response for the UI.

    Returns a list of platform dicts, each with id, label, icon, and actions.
    """
    from laya.integrations.platforms import PLATFORMS

    result: list[dict] = []

    for platform_id, caps in _CAPABILITIES.items():
        composable_actions = [
            c for c in caps if c.action_type not in _NON_COMPOSABLE_ACTIONS
        ]
        if not composable_actions:
            continue

        meta = PLATFORMS.get(platform_id, {})
        label = meta.get("label", platform_id.title())
        icon = meta.get("icon", platform_id)

        actions = []
        for cap in composable_actions:
            actions.append({
                "action_type": cap.action_type,
                "label": cap.label,
                "fields": get_compose_fields(platform_id, cap.action_type),
            })

        result.append({
            "id": platform_id,
            "label": label,
            "icon": icon,
            "actions": actions,
        })

    return result
