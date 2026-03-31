"""Platform capability registry — defines what actions each platform supports."""

from __future__ import annotations

from laya.egress.models import EgressCapability

# ---------------------------------------------------------------------------
# Platform capability definitions
# ---------------------------------------------------------------------------

_CAPABILITIES: dict[str, list[EgressCapability]] = {
    "gmail": [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["thread_id", "cc", "bcc"],
            description="Send a new email or reply to a thread.",
        ),
        EgressCapability(
            action_type="forward",
            label="Forward Email",
            requires_fields=["to", "body"],
            optional_fields=["subject", "gmail_id"],
            description="Forward an email to another recipient.",
        ),
        EgressCapability(
            action_type="archive",
            label="Archive Email",
            requires_fields=["gmail_id"],
            description="Remove email from inbox (archive).",
            confirmation_required=False,
        ),
        EgressCapability(
            action_type="star",
            label="Star Email",
            requires_fields=["gmail_id"],
            description="Star/flag an email.",
            confirmation_required=False,
        ),
        EgressCapability(
            action_type="mark_read",
            label="Mark as Read",
            requires_fields=["gmail_id"],
            description="Mark email as read.",
            confirmation_required=False,
        ),
    ],
    "outlook": [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["conversation_id", "cc", "bcc"],
            description="Send a new email or reply to a thread.",
        ),
        EgressCapability(
            action_type="forward",
            label="Forward Email",
            requires_fields=["to", "body"],
            optional_fields=["subject", "message_id"],
            description="Forward an email to another recipient.",
        ),
        EgressCapability(
            action_type="archive",
            label="Archive Email",
            requires_fields=["message_id"],
            description="Move email to archive folder.",
            confirmation_required=False,
        ),
    ],
    "smtp": [
        EgressCapability(
            action_type="send_email",
            label="Send Email",
            requires_fields=["to", "subject", "body"],
            optional_fields=["in_reply_to", "references", "cc", "bcc"],
            description="Send email via SMTP (generic email provider).",
        ),
    ],
    "jira": [
        EgressCapability(
            action_type="comment",
            label="Post Comment",
            requires_fields=["issue_key", "comment"],
            optional_fields=["visibility"],
            description="Add a comment to a Jira issue.",
        ),
        EgressCapability(
            action_type="transition",
            label="Change Status",
            requires_fields=["issue_key", "target_status"],
            optional_fields=["comment"],
            description="Transition a Jira issue to a new status.",
        ),
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["project", "summary"],
            optional_fields=["description", "type", "priority", "assignee", "labels"],
            description="Create a new Jira issue.",
        ),
        EgressCapability(
            action_type="assign",
            label="Assign Issue",
            requires_fields=["issue_key", "assignee"],
            description="Assign a Jira issue to someone.",
        ),
        EgressCapability(
            action_type="update_fields",
            label="Update Fields",
            requires_fields=["issue_key", "fields"],
            description="Update arbitrary fields on a Jira issue.",
        ),
    ],
    "github": [
        EgressCapability(
            action_type="comment",
            label="Comment on Issue",
            requires_fields=["owner", "repo", "issue_number", "comment"],
            description="Post a comment on a GitHub issue or PR.",
        ),
        EgressCapability(
            action_type="close_issue",
            label="Close Issue",
            requires_fields=["owner", "repo", "issue_number"],
            optional_fields=["comment"],
            description="Close a GitHub issue.",
        ),
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["owner", "repo", "title"],
            optional_fields=["body", "labels", "assignees"],
            description="Create a new GitHub issue.",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["comment"],
            description="Approve a pull request.",
        ),
        EgressCapability(
            action_type="request_changes",
            label="Request Changes",
            requires_fields=["owner", "repo", "pr_number", "comment"],
            description="Request changes on a pull request.",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["merge_method", "commit_title"],
            description="Merge a pull request.",
        ),
        EgressCapability(
            action_type="create_pr",
            label="Create PR",
            requires_fields=["owner", "repo", "title", "head", "base"],
            optional_fields=["body", "draft"],
            description="Create a new pull request.",
        ),
    ],
    "bitbucket": [
        EgressCapability(
            action_type="comment_pr",
            label="Comment on PR",
            requires_fields=["workspace", "repo", "pr_id", "comment"],
            description="Post a comment on a Bitbucket pull request.",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Approve a Bitbucket pull request.",
        ),
        EgressCapability(
            action_type="decline_pr",
            label="Decline PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Decline a Bitbucket pull request.",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["workspace", "repo", "pr_id"],
            optional_fields=["merge_strategy", "close_source_branch"],
            description="Merge a Bitbucket pull request.",
        ),
        EgressCapability(
            action_type="create_pr",
            label="Create PR",
            requires_fields=["workspace", "repo", "title", "source_branch", "dest_branch"],
            optional_fields=["description"],
            description="Create a new Bitbucket pull request.",
        ),
    ],
    "slack": [
        EgressCapability(
            action_type="send_message",
            label="Send Message",
            requires_fields=["channel", "message"],
            description="Send a message to a Slack channel.",
        ),
        EgressCapability(
            action_type="reply_thread",
            label="Reply to Thread",
            requires_fields=["channel", "thread_ts", "message"],
            description="Reply to a Slack thread.",
        ),
        EgressCapability(
            action_type="react",
            label="React",
            requires_fields=["channel", "timestamp", "emoji"],
            description="Add an emoji reaction to a message.",
            confirmation_required=False,
        ),
        EgressCapability(
            action_type="upload_file",
            label="Upload File",
            requires_fields=["channel", "file"],
            optional_fields=["initial_comment", "filename"],
            description="Upload a file to a Slack channel.",
        ),
    ],
    "linear": [
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["team_id", "title"],
            optional_fields=["description", "priority", "assignee_id", "state_id"],
            description="Create a new Linear issue.",
        ),
        EgressCapability(
            action_type="comment",
            label="Comment",
            requires_fields=["issue_id", "body"],
            description="Comment on a Linear issue.",
        ),
        EgressCapability(
            action_type="update_status",
            label="Update Status",
            requires_fields=["issue_id", "state_id"],
            description="Change the status of a Linear issue.",
        ),
        EgressCapability(
            action_type="assign",
            label="Assign",
            requires_fields=["issue_id", "assignee_id"],
            description="Assign a Linear issue to someone.",
        ),
    ],
    "calendar": [
        EgressCapability(
            action_type="create_event",
            label="Create Event",
            requires_fields=["title", "start", "end"],
            optional_fields=["description", "attendees", "location"],
            description="Create a calendar event.",
        ),
        EgressCapability(
            action_type="update_event",
            label="Update Event",
            requires_fields=["event_id"],
            optional_fields=["title", "start", "end", "description"],
            description="Update an existing calendar event.",
        ),
        EgressCapability(
            action_type="delete_event",
            label="Delete Event",
            requires_fields=["event_id"],
            description="Delete a calendar event.",
        ),
    ],
    "outlook_calendar": [
        EgressCapability(
            action_type="create_event",
            label="Create Event",
            requires_fields=["title", "start", "end"],
            optional_fields=["description", "attendees", "location"],
            description="Create an Outlook calendar event.",
        ),
    ],
}


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
