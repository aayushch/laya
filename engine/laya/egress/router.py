"""Egress router — resolves the best backend for each request and dispatches."""

from __future__ import annotations

import structlog

from laya.egress.backends.base import EgressBackend
from laya.egress.backends.n8n import N8nBackend
from laya.egress.backends.smtp import SmtpBackend
from laya.egress.models import EgressPreview, EgressRequest, EgressResult
from laya.egress.registry import get_capability

log = structlog.get_logger()

# Singleton backend instances
_n8n_backend = N8nBackend()
_smtp_backend = SmtpBackend()


async def route_and_execute(request: EgressRequest) -> EgressResult:
    """Resolve the best backend and execute the request."""
    backend = await _resolve_backend(request)
    if not backend:
        return EgressResult(
            success=False,
            error=(
                f"No execution backend available for platform '{request.platform}'. "
                f"Connect {request.platform} in Settings > Integrations."
            ),
        )

    if request.dry_run:
        return EgressResult(success=True, result_data={"dry_run": True})

    # Credentials are resolved by the backend itself (from n8n credential store)
    # For the n8n backend, credentials are embedded in the workflow — we don't
    # need to pass them explicitly. The SMTP backend will resolve its own creds.
    result = await backend.execute(request, credentials={})

    log.info(
        "egress_executed",
        platform=request.platform,
        action=request.action_type,
        success=result.success,
        result_url=result.result_url,
        error=result.error,
    )

    return result


async def build_preview(request: EgressRequest) -> EgressPreview:
    """Build a human-readable preview of what an action will do."""
    platform = request.platform
    action = request.action_type
    payload = request.payload

    summary = _build_summary(platform, action, payload)
    warnings = _build_warnings(platform, action, payload)

    # Determine impact level
    impact = "low"
    if action in ("merge_pr", "decline_pr", "delete_event", "transition"):
        impact = "high"
    elif action in ("send_email", "send_message", "comment", "create_issue", "create_pr"):
        impact = "medium"

    return EgressPreview(
        platform=platform,
        action_type=action,
        summary=summary,
        details=payload,
        warnings=warnings,
        estimated_impact=impact,
    )


async def _resolve_backend(request: EgressRequest) -> EgressBackend | None:
    """Resolve the best available backend for a request.

    Priority:
    1. SMTP backend for platform='smtp' (generic email — future)
    2. n8n backend for all other platforms (primary)
    """
    # SMTP backend for generic email
    if request.platform == "smtp":
        return _smtp_backend

    # Primary: n8n
    if _n8n_backend.supports(request.platform, request.action_type):
        return _n8n_backend

    # Special case: calendar actions sometimes tagged as "gmail" by stager
    if request.platform == "gmail" and request.action_type in ("calendar", "create_calendar_event"):
        request.platform = "google_calendar"
        if _n8n_backend.supports("google_calendar", request.action_type):
            return _n8n_backend

    log.warning(
        "no_backend_available",
        platform=request.platform,
        action=request.action_type,
    )
    return None


def _build_summary(platform: str, action: str, payload: dict) -> str:
    """Build a one-line human-readable summary of the action."""
    cap = get_capability(platform, action)
    label = cap.label if cap else action

    if platform in ("gmail", "outlook", "smtp") and action == "send_email":
        to = payload.get("to", "unknown")
        subject = payload.get("subject", "(no subject)")
        return f"Send email to {to} with subject '{subject}'"

    if platform in ("gmail", "outlook") and action == "forward":
        to = payload.get("to", "unknown")
        return f"Forward email to {to}"

    if platform in ("gmail", "outlook") and action == "archive":
        return "Archive email (remove from inbox)"

    if platform == "jira":
        issue = payload.get("issue_key", "unknown")
        if action == "comment":
            preview = (payload.get("comment") or "")[:60]
            return f"Post comment on {issue}: '{preview}...'" if len(payload.get("comment", "")) > 60 else f"Post comment on {issue}"
        if action == "transition":
            target = payload.get("target_status", "unknown")
            return f"Transition {issue} to '{target}'"
        if action == "create_issue":
            proj = payload.get("project", "unknown")
            title = payload.get("summary", payload.get("title", ""))
            return f"Create {payload.get('type', 'issue')} in {proj}: '{title}'"
        if action == "assign":
            return f"Assign {issue} to {payload.get('assignee', 'unknown')}"

    if platform == "github":
        owner = payload.get("owner", "")
        repo = payload.get("repo", "")
        num = payload.get("issue_number") or payload.get("pr_number", "")
        ref = f"{owner}/{repo}#{num}" if owner and repo else str(num)
        if action == "comment":
            return f"Comment on {ref}"
        if action == "close_issue":
            return f"Close {ref}"
        if action == "approve_pr":
            return f"Approve PR {ref}"
        if action == "request_changes":
            return f"Request changes on PR {ref}"
        if action == "merge_pr":
            method = payload.get("merge_method", "squash")
            return f"Merge PR {ref} ({method})"
        if action == "create_issue":
            return f"Create issue in {owner}/{repo}: '{payload.get('title', '')}'"
        if action == "create_pr":
            return f"Create PR in {owner}/{repo}: '{payload.get('title', '')}'"

    if platform == "bitbucket":
        ws = payload.get("workspace", "")
        repo = payload.get("repo", "")
        pr = payload.get("pr_id", "")
        ref = f"{ws}/{repo} PR #{pr}" if ws and repo else f"PR #{pr}"
        if action == "comment_pr":
            return f"Comment on {ref}"
        if action == "approve_pr":
            return f"Approve {ref}"
        if action == "decline_pr":
            return f"Decline {ref}"
        if action == "merge_pr":
            return f"Merge {ref}"

    if platform == "slack":
        channel = payload.get("channel", "unknown")
        if action == "reply_thread":
            return f"Reply in thread in {channel}"
        if action == "send_message":
            return f"Send message to {channel}"
        if action == "react":
            emoji = payload.get("emoji", "")
            return f"React with :{emoji}: in {channel}"

    if action in ("create_event", "update_event", "delete_event"):
        title = payload.get("title", "event")
        return f"{label}: '{title}'"

    return f"{label} on {platform}"


def _build_warnings(platform: str, action: str, payload: dict) -> list[str]:
    """Build warnings for the user about potential impact."""
    warnings = []

    if action == "merge_pr":
        warnings.append("This will merge the pull request. This action cannot be undone.")

    if action == "decline_pr":
        warnings.append("This will decline the pull request.")

    if action == "delete_event":
        warnings.append("This will permanently delete the calendar event.")

    if action == "transition" and payload.get("target_status", "").lower() in ("closed", "done", "resolved"):
        warnings.append(f"This will move the ticket to terminal status '{payload.get('target_status')}'.")

    # Email with many recipients
    if action == "send_email":
        cc = payload.get("cc", "")
        bcc = payload.get("bcc", "")
        recipients = 1  # to
        if cc:
            recipients += len(cc.split(","))
        if bcc:
            recipients += len(bcc.split(","))
        if recipients > 3:
            warnings.append(f"This email will be sent to {recipients} recipients.")

    return warnings
