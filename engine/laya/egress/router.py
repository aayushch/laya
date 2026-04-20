"""Egress router — resolves the best backend for each request and dispatches."""

from __future__ import annotations

from collections import defaultdict

import structlog

from laya.egress.backends.base import EgressBackend
from laya.egress.backends.n8n import N8nBackend
from laya.egress.backends.smtp import SmtpBackend
from laya.egress.enrichment import enrich_payload_from_event
from laya.egress.models import EgressCapability, EgressPreview, EgressRequest, EgressResult
from laya.egress.registry import get_capability
from laya.egress.summary_helpers import computed_placeholders

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
    """Build a human-readable preview of what an action will do.

    Runs the same payload enrichment (event-derived identifiers + platform
    normalization) as the execute path so the summary and details shown
    to the user reflect what will actually be dispatched.
    """
    payload, _event_ctx = await enrich_payload_from_event(request)
    cap = get_capability(request.platform, request.action_type)

    summary = _render_summary(cap, request.platform, request.action_type, payload)
    warnings = _build_warnings(cap, request.action_type, payload)
    impact = cap.impact if cap else "low"

    return EgressPreview(
        platform=request.platform,
        action_type=request.action_type,
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


def _render_summary(
    cap: EgressCapability | None,
    platform: str,
    action: str,
    payload: dict,
) -> str:
    """Render a preview summary from the capability's ``summary_template``.

    Missing payload fields render as ``"unknown"``.  Computed placeholders
    (``{gh_ref}``, ``{bb_ref}``) are injected from
    :mod:`laya.egress.summary_helpers`.  When a capability has no template
    (or there's no capability at all) we fall back to ``"<label> on <platform>"``.
    """
    if not cap or not cap.summary_template:
        label = cap.label if cap else action
        return f"{label} on {platform}"
    safe = defaultdict(
        lambda: "unknown",
        {**payload, **computed_placeholders(payload)},
    )
    return cap.summary_template.format_map(safe)


def _build_warnings(
    cap: EgressCapability | None,
    action: str,
    payload: dict,
) -> list[str]:
    """Return static warnings declared on the capability plus any dynamic
    ones whose condition depends on the current payload (terminal-status
    transitions, many-recipient emails).  Dynamic branches stay here
    because templates can't express runtime value predicates cleanly."""
    warnings: list[str] = list(cap.warnings) if cap else []

    # Dynamic: Jira-style transition moving to a terminal status.
    if action == "transition":
        target = (payload.get("target_status") or "").lower()
        if target in ("closed", "done", "resolved"):
            warnings.append(
                f"This will move the ticket to terminal status '{payload.get('target_status')}'."
            )

    # Dynamic: email going to many recipients (to + cc + bcc).
    if action == "send_email":
        cc = payload.get("cc") or ""
        bcc = payload.get("bcc") or ""
        recipients = 1  # to
        if cc:
            recipients += len([x for x in cc.split(",") if x.strip()])
        if bcc:
            recipients += len([x for x in bcc.split(",") if x.strip()])
        if recipients > 3:
            warnings.append(f"This email will be sent to {recipients} recipients.")

    return warnings
