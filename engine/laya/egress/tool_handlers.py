"""Execution handlers for egress chat tools.

These handlers are called by the chat tool loop when the LLM invokes
an egress tool. They follow a preview -> confirm -> execute pattern.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import structlog

import laya.egress as egress
from laya.egress.models import EgressRequest


log = structlog.get_logger()

# In-memory store of pending egress requests awaiting user confirmation.
# Keyed by execute_token, values are (EgressRequest, expiry_timestamp).
_pending_requests: dict[str, tuple[EgressRequest, float]] = {}

# Token TTL: 5 minutes
_TOKEN_TTL = 300

# Secret for HMAC signing (generated at startup)
_TOKEN_SECRET = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public handlers
# ---------------------------------------------------------------------------


async def handle_egress_tool(
    tool_name: str, arguments: dict, space_id: str | None
) -> str:
    """Universal handler for egress action tools (send_email, comment_on_ticket, etc.).

    Builds an EgressRequest, calls preview(), returns preview with an execute_token
    for the LLM to show the user and await confirmation.
    """
    request = _build_request(tool_name, arguments, space_id)

    # Get preview
    preview = await egress.preview(request)

    # Generate execute token and store pending request
    token = _generate_token(request)
    _pending_requests[token] = (request, time.time() + _TOKEN_TTL)

    # Clean expired tokens
    _cleanup_expired()

    return json.dumps({
        "status": "preview",
        "summary": preview.summary,
        "details": preview.details,
        "warnings": preview.warnings,
        "estimated_impact": preview.estimated_impact,
        "execute_token": token,
        "instruction": (
            "Show this preview to the user and ask for confirmation. "
            "If they confirm, call confirm_egress with the execute_token."
        ),
    })


async def handle_open_compose(
    arguments: dict, space_id: str | None
) -> str:
    """Open the compose editor in the UI via WebSocket broadcast."""
    from laya.api.websocket import manager

    await manager.broadcast({
        "type": "open_compose",
        "payload": {
            "platform": arguments["platform"],
            "action_type": arguments["action_type"],
            "prefill": arguments.get("prefill", {}),
            "source_card_id": arguments.get("source_card_id"),
        },
    })

    platform = arguments["platform"]
    action_type = arguments["action_type"]
    return json.dumps({
        "status": "compose_opened",
        "message": (
            f"Opened the {action_type} editor for {platform}. "
            "The user can edit and send from the UI."
        ),
    })


async def handle_confirm_egress(
    arguments: dict, space_id: str | None
) -> str:
    """Execute a previously previewed egress action after user confirmation."""
    token = arguments.get("execute_token", "")

    # Retrieve pending request
    entry = _pending_requests.pop(token, None)
    if not entry:
        return json.dumps({
            "status": "error",
            "error": "Token expired or already used. Ask the user to try the action again.",
        })

    request, expiry = entry
    if time.time() > expiry:
        return json.dumps({
            "status": "error",
            "error": "Token has expired. Ask the user to try the action again.",
        })

    # Execute
    result = await egress.execute(request)

    if result.success:
        response: dict[str, Any] = {
            "status": "done",
            "message": "Action executed successfully.",
        }
        if result.result_url:
            response["result_url"] = result.result_url
        if result.result_data:
            response["result_data"] = result.result_data
        return json.dumps(response)
    else:
        return json.dumps({
            "status": "failed",
            "error": result.error or "Action failed",
            "retryable": result.retryable,
        })


# ---------------------------------------------------------------------------
# Request builders (tool arguments -> EgressRequest)
# ---------------------------------------------------------------------------


def _build_request(
    tool_name: str, arguments: dict, space_id: str | None
) -> EgressRequest:
    """Map a tool name + arguments to an EgressRequest."""

    if tool_name == "send_email":
        return _build_email_request(arguments, space_id)
    elif tool_name == "comment_on_ticket":
        return _build_comment_request(arguments, space_id)
    elif tool_name == "transition_ticket":
        return _build_transition_request(arguments, space_id)
    elif tool_name == "create_ticket":
        return _build_create_ticket_request(arguments, space_id)
    elif tool_name == "pr_action":
        return _build_pr_request(arguments, space_id)
    elif tool_name == "send_slack_message":
        return _build_slack_request(arguments, space_id)
    else:
        raise ValueError(f"Unknown egress tool: {tool_name}")


def _build_email_request(args: dict, space_id: str | None) -> EgressRequest:
    platform = args.get("platform", "gmail")
    payload: dict[str, Any] = {
        "to": args["to"],
        "subject": args["subject"],
        "body": args["body"],
    }
    if args.get("thread_id"):
        payload["thread_id"] = args["thread_id"]
    if args.get("cc"):
        payload["cc"] = args["cc"]
    if args.get("bcc"):
        payload["bcc"] = args["bcc"]

    return EgressRequest(
        platform=platform,
        action_type="send_email",
        payload=payload,
        space_id=space_id,
    )


def _build_comment_request(args: dict, space_id: str | None) -> EgressRequest:
    platform = args["platform"]
    ticket_id = args["ticket_id"]
    comment = args["comment"]

    if platform == "jira":
        payload: dict[str, Any] = {"issue_key": ticket_id, "comment": comment}
    elif platform == "github":
        # Parse "owner/repo#123" format
        owner, repo, number = _parse_github_ref(ticket_id)
        payload = {"owner": owner, "repo": repo, "issue_number": number, "comment": comment}
    elif platform == "linear":
        payload = {"issue_id": ticket_id, "body": comment}
    else:
        payload = {"ticket_id": ticket_id, "comment": comment}

    return EgressRequest(
        platform=platform,
        action_type="comment",
        payload=payload,
        space_id=space_id,
    )


def _build_transition_request(args: dict, space_id: str | None) -> EgressRequest:
    platform = args["platform"]
    payload: dict[str, Any] = {
        "issue_key": args["ticket_id"],
        "target_status": args["target_status"],
    }
    if args.get("comment"):
        payload["comment"] = args["comment"]

    action_type = "transition" if platform == "jira" else "update_status"
    if platform == "linear":
        payload = {"issue_id": args["ticket_id"], "state_id": args["target_status"]}

    return EgressRequest(
        platform=platform,
        action_type=action_type,
        payload=payload,
        space_id=space_id,
    )


def _build_create_ticket_request(args: dict, space_id: str | None) -> EgressRequest:
    platform = args["platform"]

    if platform == "jira":
        payload: dict[str, Any] = {
            "project": args["project"],
            "summary": args["title"],
        }
        if args.get("description"):
            payload["description"] = args["description"]
        if args.get("type"):
            payload["type"] = args["type"]
        if args.get("priority"):
            payload["priority"] = args["priority"]
        if args.get("assignee"):
            payload["assignee"] = args["assignee"]
    elif platform == "github":
        owner, repo = args["project"].split("/", 1) if "/" in args["project"] else ("", args["project"])
        payload = {"owner": owner, "repo": repo, "title": args["title"]}
        if args.get("description"):
            payload["body"] = args["description"]
        if args.get("labels"):
            payload["labels"] = args["labels"]
        if args.get("assignee"):
            payload["assignees"] = args["assignee"]
    elif platform == "linear":
        payload = {"team_id": args["project"], "title": args["title"]}
        if args.get("description"):
            payload["description"] = args["description"]
        if args.get("priority"):
            payload["priority"] = args["priority"]
        if args.get("assignee"):
            payload["assignee_id"] = args["assignee"]
    else:
        payload = dict(args)

    return EgressRequest(
        platform=platform,
        action_type="create_issue",
        payload=payload,
        space_id=space_id,
    )


def _build_pr_request(args: dict, space_id: str | None) -> EgressRequest:
    platform = args["platform"]
    pr_id = args["pr_id"]
    action = args["action"]

    if platform == "github":
        owner, repo, number = _parse_github_ref(pr_id)
        payload: dict[str, Any] = {"owner": owner, "repo": repo, "pr_number": number}
        if args.get("comment"):
            payload["comment"] = args["comment"]
        if action == "merge":
            payload["merge_method"] = args.get("merge_strategy", "squash")

        # Map action names to egress action_types
        action_type_map = {
            "approve": "approve_pr",
            "request_changes": "request_changes",
            "comment": "comment",
            "merge": "merge_pr",
        }
        action_type = action_type_map.get(action, action)

    elif platform == "bitbucket":
        parts = pr_id.split("/")
        if len(parts) >= 3:
            workspace, repo, pr_num = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            workspace, repo = parts[0], parts[1]
            pr_num = ""
        else:
            workspace, repo, pr_num = "", "", pr_id

        payload = {"workspace": workspace, "repo": repo, "pr_id": pr_num}
        if args.get("comment"):
            payload["comment"] = args["comment"]
        if action == "merge":
            payload["merge_strategy"] = args.get("merge_strategy", "squash")

        action_type_map = {
            "approve": "approve_pr",
            "decline": "decline_pr",
            "comment": "comment_pr",
            "merge": "merge_pr",
        }
        action_type = action_type_map.get(action, action)
    else:
        payload = dict(args)
        action_type = action

    return EgressRequest(
        platform=platform,
        action_type=action_type,
        payload=payload,
        space_id=space_id,
    )


def _build_slack_request(args: dict, space_id: str | None) -> EgressRequest:
    payload: dict[str, Any] = {
        "channel": args["channel"],
        "message": args["message"],
    }

    action_type = "send_message"
    if args.get("thread_ts"):
        payload["thread_ts"] = args["thread_ts"]
        action_type = "reply_thread"

    return EgressRequest(
        platform="slack",
        action_type=action_type,
        payload=payload,
        space_id=space_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_github_ref(ref: str) -> tuple[str, str, int]:
    """Parse 'owner/repo#123' into (owner, repo, number)."""
    if "#" in ref:
        repo_part, num_str = ref.rsplit("#", 1)
        parts = repo_part.split("/")
        owner = parts[0] if len(parts) >= 2 else ""
        repo = parts[1] if len(parts) >= 2 else parts[0]
        try:
            number = int(num_str)
        except ValueError:
            number = 0
    else:
        # Try to extract just a number
        parts = ref.split("/")
        owner = parts[0] if len(parts) >= 3 else ""
        repo = parts[1] if len(parts) >= 3 else ""
        try:
            number = int(parts[-1])
        except ValueError:
            number = 0

    return owner, repo, number


def _generate_token(request: EgressRequest) -> str:
    """Generate an HMAC-signed execute token for a pending request."""
    data = f"{request.platform}:{request.action_type}:{time.time_ns()}"
    sig = hmac.new(_TOKEN_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]
    return f"egr_{sig}"


def _cleanup_expired() -> None:
    """Remove expired pending requests."""
    now = time.time()
    expired = [k for k, (_, exp) in _pending_requests.items() if now > exp]
    for k in expired:
        del _pending_requests[k]
