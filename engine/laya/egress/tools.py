"""LLM tool definitions for chat-driven egress.

These tools are added to the chat pipeline so users can trigger platform
actions via natural language in Laya chat.
"""

from __future__ import annotations


def get_egress_tool_definitions() -> list[dict]:
    """Return egress tool definitions in OpenAI function-calling format."""
    return [
        _send_email(),
        _comment_on_ticket(),
        _transition_ticket(),
        _create_ticket(),
        _pr_action(),
        _send_slack_message(),
        _open_compose(),
        _confirm_egress(),
    ]


# Tool name set for routing in executor.py
EGRESS_TOOL_NAMES = frozenset({
    "send_email",
    "comment_on_ticket",
    "transition_ticket",
    "create_ticket",
    "pr_action",
    "send_slack_message",
})


def _send_email() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email or reply to an email thread. Use when the user wants to "
                "email someone, reply to an email, or forward an email. "
                "IMPORTANT: Before calling this, use search_cards or search_events to find "
                "the relevant email thread and extract the recipient, subject, and thread_id "
                "from the card/event metadata. Do not guess email addresses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line (auto-prefixed with 'Re: ' for replies).",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text.",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Gmail/Outlook thread ID for replies (from event metadata gmail_threadId).",
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients, comma-separated.",
                    },
                    "bcc": {
                        "type": "string",
                        "description": "BCC recipients, comma-separated.",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["gmail", "outlook", "smtp"],
                        "description": "Email platform. Infer from event source_platform.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    }


def _comment_on_ticket() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "comment_on_ticket",
            "description": (
                "Post a comment on a Jira ticket, GitHub issue, or Linear issue. "
                "Use search_cards or search_events first to find the ticket and extract "
                "its platform-specific identifier from metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["jira", "github", "linear"],
                        "description": "Platform hosting the ticket.",
                    },
                    "ticket_id": {
                        "type": "string",
                        "description": (
                            "Ticket identifier. Jira: 'PROJ-123'. "
                            "GitHub: 'owner/repo#45'. Linear: issue ID."
                        ),
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment body text (supports markdown).",
                    },
                },
                "required": ["platform", "ticket_id", "comment"],
            },
        },
    }


def _transition_ticket() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "transition_ticket",
            "description": (
                "Change the status of a Jira or Linear ticket (e.g., 'In Progress' -> 'Done'). "
                "Use search_cards to find the ticket first. If the user says 'close' or 'resolve', "
                "map to the appropriate terminal status for that platform."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["jira", "linear"],
                    },
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier (e.g., 'PROJ-123').",
                    },
                    "target_status": {
                        "type": "string",
                        "description": (
                            "New status name. Common values: 'To Do', 'In Progress', "
                            "'In Review', 'Done', 'Closed', 'Resolved'. "
                            "The executor resolves this to the correct transition ID."
                        ),
                    },
                    "comment": {
                        "type": "string",
                        "description": "Optional comment to add with the transition.",
                    },
                },
                "required": ["platform", "ticket_id", "target_status"],
            },
        },
    }


def _create_ticket() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": (
                "Create a new ticket/issue on Jira, GitHub, or Linear. "
                "Use when the user says 'create a ticket', 'file an issue', 'open a bug', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["jira", "github", "linear"],
                    },
                    "project": {
                        "type": "string",
                        "description": (
                            "Project/repo. Jira: project key ('PROJ'). "
                            "GitHub: 'owner/repo'. Linear: team key."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Issue title/summary.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue description (supports markdown).",
                    },
                    "type": {
                        "type": "string",
                        "description": "Issue type: 'bug', 'task', 'story', 'epic' (Jira/Linear only).",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority: 'lowest', 'low', 'medium', 'high', 'highest'.",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Assignee email or username.",
                    },
                    "labels": {
                        "type": "string",
                        "description": "Comma-separated labels.",
                    },
                },
                "required": ["platform", "project", "title"],
            },
        },
    }


def _pr_action() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "pr_action",
            "description": (
                "Perform an action on a pull request: approve, request changes, comment, "
                "merge, or decline. Use search_cards to find the PR first and extract the "
                "PR identifier from card/event metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["github", "bitbucket"],
                    },
                    "pr_id": {
                        "type": "string",
                        "description": (
                            "PR identifier. GitHub: 'owner/repo#123'. "
                            "Bitbucket: 'workspace/repo/45'."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["approve", "request_changes", "comment", "merge", "decline"],
                        "description": "What to do with the PR.",
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment body (required for 'comment' and 'request_changes').",
                    },
                    "merge_strategy": {
                        "type": "string",
                        "enum": ["merge", "squash", "rebase"],
                        "description": "Merge strategy (only for 'merge' action, default: 'squash').",
                    },
                },
                "required": ["platform", "pr_id", "action"],
            },
        },
    }


def _send_slack_message() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "send_slack_message",
            "description": (
                "Send a Slack message to a channel or reply to a thread. "
                "Use search_cards/search_events to find the channel and thread_ts for replies. "
                "For new messages, ask the user which channel to post in."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g., '#general') or Slack channel ID.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text (supports Slack mrkdwn formatting).",
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Thread timestamp for thread replies (from slack_thread_ts in event metadata).",
                    },
                },
                "required": ["channel", "message"],
            },
        },
    }


def _open_compose() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "open_compose",
            "description": (
                "Open the compose/reply editor in the UI, pre-filled with given data. "
                "Use this when the user wants to WRITE or EDIT a message before sending — "
                "cases like 'I want to reply to...', 'draft an email to...', "
                "'help me write a response to...'. "
                "For direct commands where intent is clear ('approve PR 23', 'close PROJ-123'), "
                "use the direct action tools instead.\n\n"
                "This tool opens a UI editor. It does NOT send anything — "
                "the user reviews and sends from the UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["gmail", "outlook", "smtp", "slack", "jira", "github", "bitbucket"],
                    },
                    "action_type": {
                        "type": "string",
                        "enum": ["reply", "compose", "comment", "forward"],
                        "description": "What kind of compose to open.",
                    },
                    "prefill": {
                        "type": "object",
                        "description": (
                            "Pre-filled fields for the editor. Varies by platform:\n"
                            "Email: {to, subject, body, thread_id, cc}\n"
                            "Slack: {channel, message, thread_ts}\n"
                            "Jira/GitHub: {ticket_id, comment}\n"
                            "Bitbucket: {pr_id, comment}"
                        ),
                    },
                    "source_card_id": {
                        "type": "string",
                        "description": "Card ID that provides context for this compose.",
                    },
                },
                "required": ["platform", "action_type", "prefill"],
            },
        },
    }


def _confirm_egress() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "confirm_egress",
            "description": (
                "Execute a previously previewed egress action after the user has confirmed. "
                "Only call this AFTER you have shown the user a preview and they have said "
                "'yes', 'go ahead', 'confirm', 'do it', or similar affirmative response. "
                "The execute_token comes from the preview response of a prior egress tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "execute_token": {
                        "type": "string",
                        "description": "Signed execution token from the preview response.",
                    },
                },
                "required": ["execute_token"],
            },
        },
    }
