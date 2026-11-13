# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Card status decided by the stager LLM.

The stager says what each event does to its work item: the pull request,
ticket, thread, meeting or page the event belongs to. Its answer is a
CardStatusVerdict (models/card.py). This module turns that answer into the
status of the new card and into the list of earlier cards to close.

Nothing here looks at the platform or the event type. The code only does three
things with the stager's answer:

- It checks the answer against the state of the work item in the database. A
  card is created `done` only when the work item is concluded after the event.
- It applies the answer to earlier cards of the same entity.
- It leaves alone every card the user reopened after Laya closed it.

The state of a work item is not stored in its own table. It is read from the
`status_effect` column of the entity's cards: the most recent card whose effect
is `concludes` or `reopens` gives the state.

"Earlier" and "later" always compare `action_cards.created_at`, which holds the
time of the event on its platform, not the time Laya processed it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import structlog

from laya.config import load_settings
from laya.models.card import CardStatusVerdict
from laya.models.card_lifecycle import INACTIVE_STATUSES, transition_card_status

log = structlog.get_logger()

OPEN = "open"
CONCLUDED = "concluded"

# Values of action_cards.status_set_by.
SET_BY_LAYA = "laya"
SET_BY_USER = "user"

# Stored as status_reason on a late card when the later card that concluded the
# work item has no reason of its own.
_LATER_EVENT_REASON = "A later event concluded this work item."


def llm_card_status_enabled() -> bool:
    """Whether the stager is asked for a card status and emit applies it.

    Read from settings.json `pipeline.llm_card_status`, default True. When it is
    False every card is created with its default status and Laya closes nothing
    on its own.
    """
    return bool(load_settings().get("pipeline", {}).get("llm_card_status", True))


@dataclass
class _EntityLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Number of coroutines that hold the lock or wait for it.
    users: int = 0


_entity_locks: dict[str, _EntityLock] = {}


@asynccontextmanager
async def entity_status_lock(entity_id: str):
    """Serialise status decisions for one entity.

    Events of one entity are processed concurrently. Without this lock, two
    cards of the same entity could each read the entity's cards before the
    other was written: a card for an older event could be inserted as `ready`
    just after a later event closed every earlier card, and would then stay
    open. The emit step holds the lock from the decision until the earlier
    cards are closed. It covers database writes only, never an LLM call.

    The dictionary entry is removed when no coroutine uses it any more, so the
    dictionary does not grow with the number of entities seen.
    """
    # No await between the lookup and the increment, so two coroutines cannot
    # both create an entry for the same entity.
    entry = _entity_locks.get(entity_id)
    if entry is None:
        entry = _EntityLock()
        _entity_locks[entity_id] = entry
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0:
            _entity_locks.pop(entity_id, None)


@dataclass
class StatusDecision:
    """What emit writes for a new card and which earlier cards it closes."""

    # Final status of the new card.
    status: str
    # Values for the status_effect, status_reason and status_set_by columns.
    status_effect: str | None = None
    status_reason: str | None = None
    status_set_by: str | None = None
    # True when the event concluded the work item. Every earlier `ready` card
    # of the entity is then closed.
    close_all_earlier: bool = False
    # Earlier cards to close when the work item stays open.
    settled_card_ids: list[str] = field(default_factory=list)
    # Stored as status_reason on each earlier card that gets closed.
    closing_reason: str | None = None

    @property
    def created_done_by_laya(self) -> bool:
        return self.status == "done" and self.status_set_by == SET_BY_LAYA

    @property
    def closes_earlier_cards(self) -> bool:
        return self.close_all_earlier or bool(self.settled_card_ids)


async def work_item_state_before(
    db, entity_id: str, event_ts: str, exclude_event_id: str
) -> str:
    """State of an entity's work item just before the event at `event_ts`.

    Returns CONCLUDED when the most recent card at or before `event_ts` that
    has a `concludes` or `reopens` effect says `concludes`. Returns OPEN when
    it says `reopens`, and when no card has either effect (this includes every
    entity whose cards were all created before status effects were stored).

    Returns OPEN regardless of the effects when the user has reopened a card
    that Laya closed and that card is still active. The user disagreed with
    the conclusion, so new events are not created `done` on the strength of it.

    `exclude_event_id` is the event being processed. Its own card can already
    exist (the persona-worker path creates the row first, and a reprocess
    updates an existing row) and must not count as history.
    """
    inactive = tuple(INACTIVE_STATUSES)
    placeholders = ", ".join("?" for _ in inactive)
    reopened = await db.execute_fetchall(
        f"""SELECT 1 FROM action_cards
            WHERE entity_id = ? AND status_set_by = ?
              AND status NOT IN ({placeholders})
            LIMIT 1""",
        (entity_id, SET_BY_USER, *inactive),
    )
    if reopened:
        return OPEN

    rows = await db.execute_fetchall(
        """SELECT status_effect FROM action_cards
           WHERE entity_id = ? AND event_id != ? AND created_at <= ?
             AND status_effect IN ('concludes', 'reopens')
           ORDER BY created_at DESC, rowid DESC
           LIMIT 1""",
        (entity_id, exclude_event_id, event_ts),
    )
    if rows and rows[0]["status_effect"] == "concludes":
        return CONCLUDED
    return OPEN


async def _later_conclusion_reason(
    db, entity_id: str, event_ts: str, exclude_event_id: str
) -> str | None:
    """Reason of a card that concluded the work item at or after `event_ts`.

    Returns None when no such card exists. When one exists, the card being
    created now is for an older event that finished processing late. The
    concluding event closed every earlier `ready` card when it was emitted,
    and this card would have been one of them had it existed then.
    """
    rows = await db.execute_fetchall(
        """SELECT status_reason FROM action_cards
           WHERE entity_id = ? AND event_id != ? AND created_at >= ?
             AND status_effect = 'concludes'
           ORDER BY created_at ASC, rowid ASC
           LIMIT 1""",
        (entity_id, exclude_event_id, event_ts),
    )
    if not rows:
        return None
    return rows[0]["status_reason"] or _LATER_EVENT_REASON


async def _user_reopened(db, event_id: str) -> bool:
    """True when this event's card already exists and the user reopened it
    after Laya closed it. This happens when the user reprocesses such a card."""
    rows = await db.execute_fetchall(
        "SELECT status_set_by FROM action_cards WHERE event_id = ? LIMIT 1",
        (event_id,),
    )
    return bool(rows) and rows[0]["status_set_by"] == SET_BY_USER


async def decide_card_status(
    db,
    *,
    entity_id: str,
    event_id: str,
    event_ts: str,
    base_status: str,
    verdict: CardStatusVerdict | None,
) -> StatusDecision:
    """Decide the status of a new card and what it does to earlier cards.

    `base_status` is the status the card would get without the stager's
    answer: `ready`, or a status set by a persona worker. Only a `ready` card
    can be created `done`. A worker's status is kept as it is.

    `verdict` is None when the stager gave no usable answer. The card can then
    still be created `done` through the late-card rule below.

    The caller must hold entity_status_lock(entity_id).
    """
    effect = verdict.event_effect if verdict else None
    decision = StatusDecision(status=base_status, status_effect=effect)

    state_before = await work_item_state_before(db, entity_id, event_ts, event_id)
    if effect == "concludes":
        state_after = CONCLUDED
    elif effect == "reopens":
        state_after = OPEN
    else:
        # `none`, `standalone_notice` and a missing answer leave the work item
        # as it was.
        state_after = state_before

    if await _user_reopened(db, event_id):
        # The card keeps the marker, so later events do not close it either.
        decision.status_set_by = SET_BY_USER
    elif base_status == "ready":
        if verdict and state_after == CONCLUDED and verdict.status == "done":
            # The stager's `done` is accepted only on a concluded work item. A
            # `done` on an open work item is ignored: models tend to mark a
            # message that needs no action as done, and such a card stays open
            # until the user has read it.
            decision.status = "done"
            decision.status_reason = verdict.reason or None
            decision.status_set_by = SET_BY_LAYA
        else:
            later_reason = await _later_conclusion_reason(db, entity_id, event_ts, event_id)
            if later_reason is not None:
                decision.status = "done"
                decision.status_reason = later_reason
                decision.status_set_by = SET_BY_LAYA

    if verdict:
        if effect == "concludes":
            decision.close_all_earlier = True
            decision.closing_reason = verdict.reason or None
        elif state_after == OPEN and verdict.settled_card_ids:
            decision.settled_card_ids = list(verdict.settled_card_ids)
            decision.closing_reason = verdict.reason or None

    log.info(
        "card_status_decided",
        event_id=event_id,
        entity_id=entity_id,
        event_effect=effect,
        stager_status=verdict.status if verdict else None,
        state_before=state_before,
        state_after=state_after,
        status=decision.status,
        close_all_earlier=decision.close_all_earlier,
        settled=len(decision.settled_card_ids),
    )
    return decision


async def close_earlier_cards(
    db,
    *,
    entity_id: str,
    event_id: str,
    event_ts: str,
    decision: StatusDecision,
) -> list[dict]:
    """Set to `done` the earlier cards that `decision` says the event settles.

    Only cards that meet all of these are closed:
    - same entity, and not the card of the event being processed;
    - status `ready` (a card that is waiting for approval, running an agent,
      failed, dismissed or archived is never touched);
    - event time at or before `event_ts`;
    - not reopened by the user after Laya closed it.

    When `decision.close_all_earlier` is False, only the cards listed in
    `decision.settled_card_ids` are considered.

    Returns the closed cards as dicts with `card_id` and `header`. The caller
    must hold entity_status_lock(entity_id).
    """
    if not decision.closes_earlier_cards:
        return []

    sql = """SELECT card_id, header FROM action_cards
             WHERE entity_id = ? AND event_id != ? AND status = 'ready'
               AND created_at <= ?
               AND (status_set_by IS NULL OR status_set_by != ?)"""
    params: list = [entity_id, event_id, event_ts, SET_BY_USER]
    if not decision.close_all_earlier:
        placeholders = ", ".join("?" for _ in decision.settled_card_ids)
        sql += f" AND card_id IN ({placeholders})"
        params.extend(decision.settled_card_ids)
    rows = await db.execute_fetchall(sql, params)

    closed: list[dict] = []
    for row in rows:
        try:
            await transition_card_status(
                row["card_id"],
                "done",
                actor="pipeline",
                extra_fields={
                    "status_reason": decision.closing_reason,
                    "status_set_by": SET_BY_LAYA,
                },
            )
        except ValueError:
            # The card's status changed between the SELECT and the write, for
            # example the user dismissed it. Leave it as the user set it.
            continue
        closed.append({"card_id": row["card_id"], "header": row["header"]})

    if closed:
        log.info(
            "earlier_cards_closed",
            entity_id=entity_id,
            event_id=event_id,
            closed_count=len(closed),
            all_earlier=decision.close_all_earlier,
        )
    return closed
