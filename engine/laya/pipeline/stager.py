# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""STAGER pipeline step — synthesize worker findings into action card data."""

import json

import structlog

from laya.config import load_settings
from laya.pipeline.related_context import query_related_context
from laya.db.sqlite import get_db
from laya.db.timeutil import db_ts
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.stager import (
    TIMELINE_MAX_CARDS,
    WorkItemTimeline,
    build_stager_messages,
    get_stager_json_schema,
)
from laya.models.card import (
    LLM_ASSIGNABLE_STATUSES,
    STATUS_EFFECTS,
    ActionCardData,
    CardStatusVerdict,
    StagedOutput,
    SuggestedAction,
)
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.card_status import llm_card_status_enabled, work_item_state_before
from laya.pipeline.context_presets import get_strictness
from laya.pipeline.tags import select_prompt_tags
from laya.workers.base import WorkerResult

log = structlog.get_logger()


# The stager's `reason` sentence is stored on the card and shown in the detail
# panel. A longer answer is cut to this many characters.
_STATUS_REASON_MAX_CHARS = 300


async def _query_work_item_timeline(event: LayaEvent) -> WorkItemTimeline:
    """Fetch the earlier cards of this event's entity for the stager.

    The stager uses them for two things: to avoid repeating research that
    earlier cards already cover, and as the history it judges the new card's
    status against.

    Only cards whose event time is at or before this event's time are
    included, so an event that is processed late is judged against the history
    it actually had. Two kinds of rows are left out:
    - the card of this same event, which exists already on the persona-worker
      path and on a reprocess;
    - `pending` cards, which are placeholders of events still in the pipeline
      and have no content yet.
    """
    # LayaEvent.entity_id is the grouping key that emit and the queue also use.
    entity_id = event.entity_id
    event_ts = db_ts(event.timestamp)
    db = await get_db()
    where = (
        "ac.entity_id = ? AND ac.event_id != ? AND ac.status != 'pending' "
        "AND ac.created_at <= ?"
    )
    params = (entity_id, event.event_id, event_ts)
    rows = await db.execute_fetchall(
        f"""SELECT ac.card_id, ac.header, ac.summary, ac.status, ac.created_at,
                   e.source_raw_event_type, e.actor_name
            FROM action_cards ac
            JOIN events e ON e.event_id = ac.event_id
            WHERE {where}
            ORDER BY ac.created_at DESC, ac.rowid DESC
            LIMIT ?""",
        (*params, TIMELINE_MAX_CARDS),
    )
    cards = [dict(r) for r in reversed(rows)]

    omitted = 0
    if len(cards) == TIMELINE_MAX_CARDS:
        count_rows = await db.execute_fetchall(
            f"""SELECT COUNT(*) AS cnt
                FROM action_cards ac
                JOIN events e ON e.event_id = ac.event_id
                WHERE {where}""",
            params,
        )
        omitted = max(0, count_rows[0]["cnt"] - len(cards))

    prior_state = await work_item_state_before(db, entity_id, event_ts, event.event_id)
    return WorkItemTimeline(
        cards=cards, omitted=omitted, prior_state=prior_state, event_time=event_ts
    )


async def run_stager(
    event: LayaEvent,
    router_output: RouterOutput,
    worker_results: list[WorkerResult] | None = None,
    space_id: str | None = None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
) -> ActionCardData:
    """Run the STAGER step: synthesize findings into a polished action card.

    Args:
        event: The original event.
        router_output: Router classification and entities.
        worker_results: Optional findings from workers (None for simple events).
        space_id: Optional space for model/key overrides.

    Returns:
        ActionCardData ready for the EMIT step.
    """
    # 1. Query ChromaDB for related context
    related_context = await query_related_context(event, n_results=3)

    # 1b. Query the earlier cards of this entity (prevents redundant research
    # and gives the stager the history it judges the card's status against).
    timeline = await _query_work_item_timeline(event)
    card_status = llm_card_status_enabled()

    # 1c. Pick the existing tag names this event is most likely to need, so
    # the LLM reuses them. A failure here only costs the hint: the stager then
    # runs without a tag list.
    existing_tags: list[str] = []
    try:
        db = await get_db()
        existing_tags = await select_prompt_tags(db, event)
    except Exception as e:
        log.warning("stager_tag_selection_failed", event_id=event.event_id, error=str(e))

    # 2. Build messages and call LLM
    _sg = load_settings().get("smart_grouping", {})
    _strictness = get_strictness(_sg)
    messages = build_stager_messages(
        event, router_output, worker_results, related_context, timeline,
        user_identity=user_identity, actor_relationship=actor_relationship,
        participant_roles=participant_roles,
        existing_tags=existing_tags,
        strictness=_strictness,
        card_status=card_status,
    )
    schema = get_stager_json_schema(card_status=card_status)

    # Transport/LLM-call failures propagate so the durable queue retries the
    # event, exactly like run_router. Previously any exception here (e.g. a
    # 30-second LMStudio restart mid-request) was swallowed into a degraded
    # fallback card and the event was marked completed — the fallback is now
    # reserved for genuine PARSE failures below (review §2 pipeline).
    response = await llm_call(
        role="stager",
        messages=messages,
        response_schema=schema,
        event_id=event.event_id,
        step="stage",
        temperature=0.2,
        max_tokens=DEFAULT_MAX_TOKENS,
        space_id=space_id,
    )

    # 3. Parse response into ActionCardData
    status_timeline = timeline if card_status else None
    if response.parsed:
        return _parse_stager_response(response.parsed, event, status_timeline)

    # Fallback: try raw content (parse failure only, not a transport failure)
    try:
        parsed = json.loads(response.content)
        return _parse_stager_response(parsed, event, status_timeline)
    except (json.JSONDecodeError, Exception) as e:
        log.error("stager_parse_failed", event_id=event.event_id, error=str(e))
        return _build_fallback_card(event, router_output)


_CROSS_PLATFORM_ALLOWED: dict[tuple[str, str], bool] = {
    # (event_platform, target_platform) combinations accepted as legitimate.
    # Same-platform pairs are always allowed; this map covers exceptions only.
    ("gmail", "outlook"): True,
    ("outlook", "gmail"): True,
}


def _is_platform_compatible(event_platform: str, target_platform: str) -> bool:
    """Suggested actions should target the event's own platform. A tiny
    allowlist permits mail-to-mail forwarding; everything else is rejected
    so the LLM cannot suggest unrelated platforms (e.g. google_calendar on
    an outlook payment confirmation)."""
    if not target_platform:
        return False
    if event_platform == target_platform:
        return True
    return _CROSS_PLATFORM_ALLOWED.get((event_platform, target_platform), False)


def _parse_card_status(
    raw: object, timeline: WorkItemTimeline, event_id: str
) -> CardStatusVerdict | None:
    """Validate the stager's `card_status` object.

    Returns None when the object is missing, or when `event_effect` or
    `status` is not one of the values the stager is allowed to return (for
    example a status the model invented, or one that only the user or Laya's
    own code may set). The card is then created with its default status.

    `settled_refs` holds "#" numbers from the timeline block. Each number is
    mapped to the card_id at that position. Entries that are not integers, or
    that point at no timeline line, are dropped.
    """
    if not isinstance(raw, dict):
        return None
    effect = raw.get("event_effect")
    status = raw.get("status")
    if effect not in STATUS_EFFECTS or status not in LLM_ASSIGNABLE_STATUSES:
        log.warning(
            "stager_card_status_ignored",
            event_id=event_id,
            event_effect=effect,
            status=status,
        )
        return None

    settled_card_ids: list[str] = []
    refs = raw.get("settled_refs")
    if isinstance(refs, list):
        for ref in refs:
            # bool is a subclass of int in Python, so exclude it explicitly.
            if isinstance(ref, bool) or not isinstance(ref, int):
                continue
            if 1 <= ref <= len(timeline.cards):
                card_id = timeline.cards[ref - 1]["card_id"]
                if card_id not in settled_card_ids:
                    settled_card_ids.append(card_id)

    reason = raw.get("reason")
    reason = " ".join(reason.split()) if isinstance(reason, str) else ""
    return CardStatusVerdict(
        event_effect=effect,
        status=status,
        reason=reason[:_STATUS_REASON_MAX_CHARS],
        settled_card_ids=settled_card_ids,
    )


def _parse_stager_response(
    data: dict, event: LayaEvent, timeline: WorkItemTimeline | None = None
) -> ActionCardData:
    """Parse the LLM response dict into ActionCardData.

    `timeline` is the timeline the stager was shown. Pass None when the stager
    was not asked for a card status; the `card_status` object is then ignored.
    """
    event_platform = event.source.platform
    actions = []
    dropped: list[dict] = []
    for act in data.get("suggested_actions", []):
        payload = act.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}

        target_platform = act.get("target_platform", "")
        if not _is_platform_compatible(event_platform, target_platform):
            dropped.append({
                "action_id": act.get("action_id"),
                "target_platform": target_platform,
                "action_type": act.get("action_type"),
            })
            continue

        try:
            actions.append(
                SuggestedAction(
                    action_id=act["action_id"],
                    label=act["label"],
                    action_type=act["action_type"],
                    target_platform=target_platform,
                    payload=payload,
                )
            )
        except Exception as e:
            # A single malformed action (missing key / bad type — common on
            # best-effort agent backends emitting partial JSON) must not raise
            # KeyError and burn all 3 queue retries before dead-lettering. Drop
            # the bad action and keep the good ones (review §2 pipeline — P4-5).
            dropped.append({
                "action_id": act.get("action_id"),
                "target_platform": target_platform,
                "reason": f"malformed: {e}",
            })
            continue

    if dropped:
        log.info(
            "stager_dropped_cross_platform_actions",
            event_id=event.event_id,
            event_platform=event_platform,
            dropped=dropped,
        )

    staged = data.get("staged_output", {})
    raw_tags = data.get("suggested_tags", [])
    suggested_tags = [t.strip().lower() for t in raw_tags if isinstance(t, str) and t.strip()][:3]
    return ActionCardData(
        header=data.get("header", event.subject.title)[:80],
        summary=data.get("summary", ""),
        intelligence_report=data.get("intelligence_report", []),
        staged_output=StagedOutput(
            type=staged.get("type", "summary"),
            content=staged.get("content", ""),
        ),
        suggested_actions=actions,
        privacy_tier=max(1, min(3, int(data.get("privacy_tier", 2)))),
        suggested_tags=suggested_tags,
        # Thread the stager's context match through so emit.py can skip the
        # fallback ChromaDB search + confirm_context_link call (review §1.1).
        context_match=data.get("context_match") if isinstance(data.get("context_match"), dict) else None,
        status_verdict=(
            _parse_card_status(data.get("card_status"), timeline, event.event_id)
            if timeline is not None
            else None
        ),
    )


def _build_fallback_card(event: LayaEvent, router_output: RouterOutput) -> ActionCardData:
    """Build a minimal card when the stager LLM fails."""
    return ActionCardData(
        header=f"Review: {event.subject.title}"[:80],
        summary=(
            f"New {router_output.category.value.lower()} event from "
            f"{event.source.platform}: {event.subject.title}"
        ),
        intelligence_report=[
            f"Source: {event.source.platform} ({event.source.raw_event_type})",
            f"Actor: {event.actor.name}",
            f"Priority: {router_output.priority.value}",
        ],
        staged_output=StagedOutput(type="summary", content=event.content.body[:500]),
        suggested_actions=[],
        privacy_tier=2,
    )
