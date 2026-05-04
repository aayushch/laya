"""Event-based payload enrichment — shared by execute and preview paths.

This module turns a raw ``EgressRequest`` payload (as emitted by the stager
LLM or a chat tool call) into an enriched payload with identifier fields
filled from the source event and with platform-specific field aliasing
applied.

The same enrichment must run at both preview time (so confirmation dialogs
display the correct summary) and execute time (so the executor receives
the correct identifiers).  Previously this logic lived inside
``N8nBackend._build_payload``; extracting it into a module-level helper
prevents preview/execute drift.

No HTTP or backend-specific logic lives here — just event-context fetch,
identifier derivation, and platform normalization.
"""

from __future__ import annotations

import json

import structlog

from laya.config import load_team
from laya.db.sqlite import get_db
from laya.egress import platforms
from laya.egress.models import EgressRequest
from laya.models.team import TeamConfig, TeamRole

log = structlog.get_logger()


async def enrich_payload_from_event(request: EgressRequest) -> tuple[dict, dict]:
    """Return ``(enriched_payload, event_ctx)`` for an egress request.

    Flow:
    1. Copy the caller payload and coerce ``None`` → ``""`` for common
       string fields (n8n JS concatenates these).
    2. Fetch the event row from SQLite (``source_event_id``) and parse
       its ``content_metadata`` JSON.
    3. Delegate to the platform helper module:
       - ``identifiers_from_event(...)`` — deterministic derivation;
         engine-wins precedence (derived values overwrite LLM ones).
       - ``normalize_payload(...)`` — LLM-variant aliasing + defaults.
    4. Return the enriched payload alongside the event context (callers
       may use ``event_ctx`` for additional envelope fields).

    When no source event exists, the payload is returned after
    normalization only; identifier derivation is skipped.
    """
    payload = dict(request.payload)

    # Normalise None values to empty strings for n8n JS compatibility.
    for key in (
        "body", "subject", "to", "message", "comment",
        "content", "title", "description",
    ):
        if key in payload and payload[key] is None:
            payload[key] = ""

    event_ctx = await _fetch_event_context(request.source_event_id)
    try:
        content_metadata = json.loads(event_ctx.get("content_metadata") or "{}")
    except (json.JSONDecodeError, AttributeError, TypeError):
        content_metadata = {}

    mod = platforms.for_platform(request.platform)
    if mod is not None:
        if request.source_event_id and event_ctx:
            kwargs: dict = {}
            if request.platform in ("gmail", "outlook"):
                kwargs["self_emails"] = _get_self_emails()
            derived = mod.identifiers_from_event(
                request.action_type,
                request.source_event_id,
                content_metadata,
                event_ctx,
                **kwargs,
            )
            if derived:
                payload = {**payload, **derived}
        payload = mod.normalize_payload(request.action_type, payload)

    return payload, event_ctx


async def _fetch_event_context(event_id: str | None) -> dict:
    """Fetch the originating event's row for enriching egress payloads.

    Returns an empty dict when no event is attached or the event has been
    purged.  ``subject_id`` is included because several platform helpers
    (e.g. ``jira.identifiers_from_event``) rely on it as the canonical
    issue key.
    """
    if not event_id:
        return {}

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT actor_email, actor_name, subject_id, subject_title,
                  source_platform, content_metadata
           FROM events WHERE event_id = ?""",
        (event_id,),
    )
    if not rows:
        return {}

    ctx = dict(rows[0])

    # Fall back to metadata for actor email (e.g., gmail_from).
    if not ctx.get("actor_email"):
        try:
            meta = json.loads(ctx.get("content_metadata") or "{}")
            ctx["actor_email"] = (
                meta.get("gmail_from")
                or meta.get("outlook_from")
                or meta.get("from")
                or ""
            )
        except (json.JSONDecodeError, AttributeError):
            pass

    return ctx


def _get_self_emails() -> set[str]:
    """Return lowercase emails (primary + aliases) for the 'self' team member.

    Used by the email platform helpers to avoid defaulting ``to`` back to
    the Laya user when they sent the triggering message themselves.
    """
    try:
        team = TeamConfig(**load_team())
        for m in team.members:
            if m.role == TeamRole.self_:
                emails = {m.email.lower()}
                emails.update(a.lower() for a in m.aliases)
                return emails
    except Exception:
        pass
    return set()


async def get_prefill_for_card(card_id: str) -> tuple[str, dict, str | None]:
    """Return ``(platform, prefill_dict, event_id)`` for a card.

    Used by the card-context endpoint to pre-fill ComposeModal fields
    with identifiers derived from the card's source event.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT event_id, entity_id FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        return "", {}, None

    event_id = rows[0]["event_id"]
    entity_id = rows[0]["entity_id"] or ""

    parts = entity_id.split(":", 2)
    platform = parts[0] if parts else ""
    if not platform:
        return "", {}, event_id

    event_ctx = await _fetch_event_context(event_id)
    if not event_ctx:
        return platform, {}, event_id

    try:
        content_metadata = json.loads(event_ctx.get("content_metadata") or "{}")
    except (json.JSONDecodeError, AttributeError, TypeError):
        content_metadata = {}

    mod = platforms.for_platform(platform)
    if mod is None:
        return platform, {}, event_id

    kwargs: dict = {}
    if platform in ("gmail", "outlook"):
        kwargs["self_emails"] = _get_self_emails()

    # Derive identifiers using a generic action_type per platform so we
    # capture the broadest set of fields.  For GitHub we call twice
    # (issue vs PR) and merge, since the action_type controls whether
    # issue_number or pr_number is returned.
    prefill: dict = {}
    probe_actions = ["comment"]
    if platform == "github":
        probe_actions = ["comment", "approve_pr"]
    elif platform == "bitbucket":
        probe_actions = ["comment_pr"]

    for action_type in probe_actions:
        derived = mod.identifiers_from_event(
            action_type, event_id, content_metadata, event_ctx, **kwargs,
        )
        if derived:
            prefill.update(derived)

    return platform, prefill, event_id
