# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for card status decided by the stager LLM.

Covers the three places the feature lives in:
- pipeline/stager.py: the timeline query and the validation of the stager's
  `card_status` object;
- pipeline/card_status.py through run_emit: the status of the new card and the
  closing of earlier cards;
- api/cards_lifecycle.py and pipeline/processing_rules.py: what the user's
  reopen and done actions, and a rule's set_status action, do to a card Laya
  closed.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.llm.prompts.stager import TIMELINE_MAX_CARDS, WorkItemTimeline
from laya.models.card import ActionCardData, CardStatusVerdict, StagedOutput
from laya.pipeline.card_status import (
    CONCLUDED,
    OPEN,
    entity_status_lock,
    work_item_state_before,
)
from laya.pipeline.emit import run_emit
from laya.pipeline.stager import (
    _build_fallback_card,
    _parse_card_status,
    _parse_stager_response,
    _query_work_item_timeline,
)
from laya.workers.base import WorkerResult
from tests.conftest import insert_test_event


def _stager_output(verdict: CardStatusVerdict | None, header: str = "Card") -> ActionCardData:
    return ActionCardData(
        header=header,
        summary="Summary of the event.",
        intelligence_report=["one finding"],
        staged_output=StagedOutput(type="summary", content="content"),
        status_verdict=verdict,
    )


def _verdict(effect: str, status: str, reason: str = "the evidence", settled=None) -> CardStatusVerdict:
    return CardStatusVerdict(
        event_effect=effect, status=status, reason=reason, settled_card_ids=settled or []
    )


def _at(sample_event, event_id: str, hour: int, minute: int = 0):
    """A copy of sample_event (same entity) with its own id and event time."""
    return sample_event.model_copy(update={
        "event_id": event_id,
        "timestamp": datetime(2026, 2, 22, hour, minute, 0, tzinfo=timezone.utc),
    })


async def _emit(db, event, router_output, verdict, *, worker_results=None, card_id=None):
    """Run run_emit for one event with every external side effect patched out.

    Returns (card_id, broadcast mock, mock of the daily-summary status update).
    """
    existing = await db.execute_fetchall(
        "SELECT 1 FROM events WHERE event_id = ?", (event.event_id,)
    )
    if not existing:
        await insert_test_event(db, event.event_id)
    with ExitStack() as stack:
        stack.enter_context(patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock))
        stack.enter_context(patch(
            "laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]
        ))
        # emit and card_lifecycle share one `manager` object, so this one patch
        # also captures the card_updated broadcasts of closed earlier cards.
        broadcast = stack.enter_context(
            patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock)
        )
        stack.enter_context(patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock))
        status_update = stack.enter_context(
            patch("laya.pipeline.emit.trigger_summary_status_update", new_callable=AsyncMock)
        )
        stack.enter_context(patch(
            "laya.pipeline.context_grouping.resolve_context_group",
            new_callable=AsyncMock, return_value=None,
        ))
        stack.enter_context(patch(
            "laya.pipeline.group_summary.trigger_group_summary_update", new_callable=AsyncMock
        ))
        new_card_id = await run_emit(
            event, router_output, _stager_output(verdict, header=event.event_id),
            worker_results=worker_results, card_id=card_id,
        )
    return new_card_id, broadcast, status_update


async def _card(db, card_id: str) -> dict:
    rows = await db.execute_fetchall(
        """SELECT status, status_effect, status_reason, status_set_by, resolved_at,
                  previous_status, read_at
           FROM action_cards WHERE card_id = ?""",
        (card_id,),
    )
    return dict(rows[0])


# ---------------------------------------------------------------------------
# Validation of the stager's answer
# ---------------------------------------------------------------------------


def _timeline(n: int = 3) -> WorkItemTimeline:
    return WorkItemTimeline(
        cards=[{"card_id": f"card_{i}", "status": "ready"} for i in range(1, n + 1)],
        prior_state="open",
        event_time="2026-02-22 14:30:00",
    )


class TestParseCardStatus:
    def test_valid_answer(self):
        verdict = _parse_card_status(
            {"reason": "  PR was\nmerged. ", "event_effect": "concludes", "status": "done",
             "settled_refs": []},
            _timeline(), "evt",
        )
        assert verdict == CardStatusVerdict(
            event_effect="concludes", status="done", reason="PR was merged.",
        )

    def test_missing_object(self):
        assert _parse_card_status(None, _timeline(), "evt") is None
        assert _parse_card_status("done", _timeline(), "evt") is None

    @pytest.mark.parametrize("status", [
        "completed", "failed", "dismissed", "archived", "pending",
        "requires_approval", "agent_running", "awaiting_input", "executing", "", None,
    ])
    def test_status_the_stager_may_not_assign_drops_the_answer(self, status):
        """A status the model invented, or one only the user or Laya's own code
        may set, makes the whole answer unusable."""
        raw = {"reason": "r", "event_effect": "concludes", "status": status, "settled_refs": []}
        assert _parse_card_status(raw, _timeline(), "evt") is None

    @pytest.mark.parametrize("effect", ["concluded", "closes", "", None, 1])
    def test_unknown_effect_drops_the_answer(self, effect):
        raw = {"reason": "r", "event_effect": effect, "status": "done", "settled_refs": []}
        assert _parse_card_status(raw, _timeline(), "evt") is None

    def test_refs_are_mapped_to_card_ids(self):
        raw = {"reason": "r", "event_effect": "none", "status": "ready", "settled_refs": [3, 1]}
        verdict = _parse_card_status(raw, _timeline(), "evt")
        assert verdict.settled_card_ids == ["card_3", "card_1"]

    def test_invalid_refs_are_dropped(self):
        raw = {
            "reason": "r", "event_effect": "none", "status": "ready",
            # 0 and 4 match no timeline line, "2" and 2.0 are not integers, True is
            # a bool, and the second 2 is a duplicate.
            "settled_refs": [0, 4, -1, "2", 2.0, True, None, 2, 2],
        }
        verdict = _parse_card_status(raw, _timeline(), "evt")
        assert verdict.settled_card_ids == ["card_2"]

    def test_refs_not_a_list(self):
        raw = {"reason": "r", "event_effect": "none", "status": "ready", "settled_refs": "1"}
        assert _parse_card_status(raw, _timeline(), "evt").settled_card_ids == []

    def test_long_reason_is_cut(self):
        raw = {"reason": "x" * 1000, "event_effect": "none", "status": "ready", "settled_refs": []}
        assert len(_parse_card_status(raw, _timeline(), "evt").reason) == 300


class TestParseStagerResponse:
    _DATA = {
        "header": "H", "summary": "S", "intelligence_report": [],
        "staged_output": {"type": "summary", "content": "c"},
        "suggested_actions": [], "privacy_tier": 2, "suggested_tags": [],
        "card_status": {"reason": "merged", "event_effect": "concludes", "status": "done",
                        "settled_refs": []},
    }

    def test_verdict_is_parsed_when_a_timeline_is_given(self, sample_event):
        card = _parse_stager_response(self._DATA, sample_event, _timeline())
        assert card.status_verdict.event_effect == "concludes"

    def test_verdict_is_ignored_without_a_timeline(self, sample_event):
        """No timeline means the stager was not asked for a status (the feature
        is switched off), so an answer in the response is not used."""
        card = _parse_stager_response(self._DATA, sample_event)
        assert card.status_verdict is None

    def test_fallback_card_has_no_verdict(self, sample_event, sample_router_output_engineer):
        card = _build_fallback_card(sample_event, sample_router_output_engineer)
        assert card.status_verdict is None


# ---------------------------------------------------------------------------
# The timeline the stager is shown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWorkItemTimelineQuery:
    async def test_earlier_cards_oldest_first(self, db, sample_event, sample_router_output_engineer):
        first, _, _ = await _emit(db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, None)
        second, _, _ = await _emit(db, _at(sample_event, "evt_b", 10), sample_router_output_engineer, None)

        timeline = await _query_work_item_timeline(_at(sample_event, "evt_c", 11))

        assert [c["card_id"] for c in timeline.cards] == [first, second]
        assert timeline.omitted == 0
        assert timeline.prior_state == OPEN
        assert timeline.event_time == "2026-02-22 11:00:00"
        assert timeline.cards[0]["source_raw_event_type"] == "issue_assigned"
        assert timeline.cards[0]["actor_name"] == "Sarah"

    async def test_later_cards_and_own_card_are_excluded(
        self, db, sample_event, sample_router_output_engineer,
    ):
        earlier, _, _ = await _emit(db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, None)
        await _emit(db, _at(sample_event, "evt_later", 12), sample_router_output_engineer, None)
        own = _at(sample_event, "evt_own", 10)
        await _emit(db, own, sample_router_output_engineer, None)

        timeline = await _query_work_item_timeline(own)

        assert [c["card_id"] for c in timeline.cards] == [earlier]

    async def test_pending_placeholder_cards_are_excluded(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, None)
        await db.execute("UPDATE action_cards SET status = 'pending' WHERE card_id = ?", (card_id,))
        await db.commit()

        timeline = await _query_work_item_timeline(_at(sample_event, "evt_b", 10))

        assert timeline.cards == []

    async def test_cap_keeps_the_most_recent_cards(
        self, db, sample_event, sample_router_output_engineer,
    ):
        total = TIMELINE_MAX_CARDS + 2
        for i in range(total):
            await _emit(
                db, _at(sample_event, f"evt_{i:02d}", 1, i), sample_router_output_engineer, None,
            )

        timeline = await _query_work_item_timeline(_at(sample_event, "evt_new", 9))

        assert len(timeline.cards) == TIMELINE_MAX_CARDS
        assert timeline.omitted == 2
        headers = [c["header"] for c in timeline.cards]
        assert headers[0] == "evt_02" and headers[-1] == f"evt_{total - 1:02d}"

    async def test_prior_state_reflects_a_stored_conclusion(
        self, db, sample_event, sample_router_output_engineer,
    ):
        await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        timeline = await _query_work_item_timeline(_at(sample_event, "evt_b", 10))
        assert timeline.prior_state == CONCLUDED


# ---------------------------------------------------------------------------
# Status of the new card and closing of earlier cards (through run_emit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEmitAppliesVerdict:
    async def test_no_verdict_creates_a_ready_card(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(db, sample_event, sample_router_output_engineer, None)
        card = await _card(db, card_id)
        assert card["status"] == "ready"
        assert card["status_effect"] is None
        assert card["status_reason"] is None
        assert card["status_set_by"] is None

    async def test_concluding_event_creates_a_done_card(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, broadcast, _ = await _emit(
            db, sample_event, sample_router_output_engineer,
            _verdict("concludes", "done", "The pull request was merged."),
        )
        card = await _card(db, card_id)
        assert card["status"] == "done"
        assert card["status_effect"] == "concludes"
        assert card["status_reason"] == "The pull request was merged."
        assert card["status_set_by"] == "laya"
        assert card["resolved_at"] is not None
        # Reopen restores previous_status, so it must say `ready`.
        assert card["previous_status"] == "ready"
        # A card Laya closed still counts as unread.
        assert card["read_at"] is None

        created = next(
            c.args[0] for c in broadcast.call_args_list if c.args[0]["type"] == "card_created"
        )
        assert created["payload"]["status"] == "done"
        assert created["payload"]["status_reason"] == "The pull request was merged."
        assert created["payload"]["status_set_by"] == "laya"

    async def test_concluding_event_closes_every_earlier_ready_card(
        self, db, sample_event, sample_router_output_engineer,
    ):
        first, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        second, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer, _verdict("none", "ready"),
        )
        _, _, status_update = await _emit(
            db, _at(sample_event, "evt_c", 11), sample_router_output_engineer,
            _verdict("concludes", "done", "Merged."),
        )

        for card_id in (first, second):
            card = await _card(db, card_id)
            assert card["status"] == "done"
            assert card["status_reason"] == "Merged."
            assert card["status_set_by"] == "laya"
            assert card["previous_status"] == "ready"
            assert card["read_at"] is None
        # The daily summary is told about each closed card.
        assert {c.args[0] for c in status_update.call_args_list} == {first, second}

    async def test_concluding_card_that_stays_ready_still_closes_earlier_cards(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """The stager may keep the concluding card `ready` when the card itself
        carries a new request. The work item is concluded all the same."""
        earlier, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        closing, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
            _verdict("concludes", "ready"),
        )
        assert (await _card(db, closing))["status"] == "ready"
        assert (await _card(db, earlier))["status"] == "done"

    async def test_only_ready_cards_are_closed(
        self, db, sample_event, sample_router_output_engineer,
    ):
        statuses = ["requires_approval", "agent_running", "awaiting_input", "executing",
                    "failed", "dismissed", "archived", "pending"]
        cards = {}
        for i, status in enumerate(statuses):
            card_id, _, _ = await _emit(
                db, _at(sample_event, f"evt_{i}", 9, i), sample_router_output_engineer, None,
            )
            await db.execute("UPDATE action_cards SET status = ? WHERE card_id = ?", (status, card_id))
            cards[card_id] = status
        await db.commit()

        await _emit(
            db, _at(sample_event, "evt_close", 11), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )

        for card_id, status in cards.items():
            assert (await _card(db, card_id))["status"] == status

    async def test_cards_of_other_entities_are_not_closed(
        self, db, sample_event, sample_router_output_engineer,
    ):
        other = sample_event.model_copy(update={
            "event_id": "evt_other",
            "timestamp": datetime(2026, 2, 22, 9, 0, 0, tzinfo=timezone.utc),
            "subject": sample_event.subject.model_copy(update={"id": "BUG-9999"}),
        })
        other_card, _, _ = await _emit(db, other, sample_router_output_engineer, None)

        await _emit(
            db, _at(sample_event, "evt_close", 11), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )

        assert (await _card(db, other_card))["status"] == "ready"

    async def test_done_is_ignored_while_the_work_item_is_open(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """Models tend to mark a message that needs no action as done. Without a
        concluded work item the card stays ready."""
        for effect in ("none", "standalone_notice", "reopens"):
            event = sample_event.model_copy(update={
                "event_id": f"evt_{effect}",
                "subject": sample_event.subject.model_copy(update={"id": f"ID-{effect}"}),
            })
            card_id, _, _ = await _emit(
                db, event, sample_router_output_engineer, _verdict(effect, "done"),
            )
            card = await _card(db, card_id)
            assert card["status"] == "ready", effect
            assert card["status_effect"] == effect
            assert card["status_reason"] is None
            assert card["status_set_by"] is None

    async def test_event_on_a_concluded_work_item_is_created_done(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """A field edit or a remark on a closed item: the effect is `none`, the
        work item was already concluded, so the card is done."""
        await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        for i, effect in enumerate(("none", "standalone_notice")):
            card_id, _, _ = await _emit(
                db, _at(sample_event, f"evt_after_{i}", 10 + i), sample_router_output_engineer,
                _verdict(effect, "done", "Edit on a closed ticket."),
            )
            card = await _card(db, card_id)
            assert card["status"] == "done", effect
            assert card["status_reason"] == "Edit on a closed ticket."

    async def test_stager_can_keep_a_card_ready_on_a_concluded_work_item(
        self, db, sample_event, sample_router_output_engineer,
    ):
        await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        card_id, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
            _verdict("none", "ready"),
        )
        assert (await _card(db, card_id))["status"] == "ready"

    async def test_reopen_then_conclude_again(
        self, db, sample_event, sample_router_output_engineer,
    ):
        first, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        reopened, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
            _verdict("reopens", "ready"),
        )
        # The reopening event makes the work item open again but does not reopen
        # the card that was closed earlier.
        assert (await _card(db, reopened))["status"] == "ready"
        assert (await _card(db, first))["status"] == "done"

        between, _, _ = await _emit(
            db, _at(sample_event, "evt_c", 11), sample_router_output_engineer,
            _verdict("none", "done"),
        )
        assert (await _card(db, between))["status"] == "ready"

        await _emit(
            db, _at(sample_event, "evt_d", 12), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        assert (await _card(db, reopened))["status"] == "done"
        assert (await _card(db, between))["status"] == "done"

    async def test_settled_refs_close_only_the_named_cards(
        self, db, sample_event, sample_router_output_engineer,
    ):
        request, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        other, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer, _verdict("none", "ready"),
        )
        approval, _, _ = await _emit(
            db, _at(sample_event, "evt_c", 11), sample_router_output_engineer,
            _verdict("none", "ready", "The approval answers the review request.", settled=[request]),
        )

        closed = await _card(db, request)
        assert closed["status"] == "done"
        assert closed["status_reason"] == "The approval answers the review request."
        assert (await _card(db, other))["status"] == "ready"
        assert (await _card(db, approval))["status"] == "ready"

    async def test_settled_refs_cannot_close_a_card_of_another_entity(
        self, db, sample_event, sample_router_output_engineer,
    ):
        other = sample_event.model_copy(update={
            "event_id": "evt_other",
            "timestamp": datetime(2026, 2, 22, 9, 0, 0, tzinfo=timezone.utc),
            "subject": sample_event.subject.model_copy(update={"id": "BUG-9999"}),
        })
        other_card, _, _ = await _emit(db, other, sample_router_output_engineer, None)

        await _emit(
            db, _at(sample_event, "evt_c", 11), sample_router_output_engineer,
            _verdict("none", "ready", settled=[other_card]),
        )

        assert (await _card(db, other_card))["status"] == "ready"

    async def test_worker_status_is_kept(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """A status set by a persona worker wins over the stager's `done`, but
        the event still concludes the work item for earlier cards."""
        earlier, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        worker = WorkerResult(persona="ENGINEER", card_status="requires_approval")
        card_id, _, _ = await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
            _verdict("concludes", "done"), worker_results=[worker],
        )
        card = await _card(db, card_id)
        assert card["status"] == "requires_approval"
        assert card["status_effect"] == "concludes"
        assert card["status_set_by"] is None
        assert (await _card(db, earlier))["status"] == "done"

    async def test_late_older_event_is_created_done(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """Events of one entity can finish processing out of order. The merge
        (11:00) is emitted first; the card of the 09:00 push is emitted after it
        and is created done with the merge's reason."""
        await _emit(
            db, _at(sample_event, "evt_merge", 11), sample_router_output_engineer,
            _verdict("concludes", "done", "Merged."),
        )
        late, _, _ = await _emit(
            db, _at(sample_event, "evt_push", 9), sample_router_output_engineer,
            _verdict("none", "ready"),
        )
        card = await _card(db, late)
        assert card["status"] == "done"
        assert card["status_reason"] == "Merged."
        assert card["status_set_by"] == "laya"
        assert card["previous_status"] == "ready"

    async def test_late_older_event_without_a_verdict_is_created_done(
        self, db, sample_event, sample_router_output_engineer,
    ):
        await _emit(
            db, _at(sample_event, "evt_merge", 11), sample_router_output_engineer,
            _verdict("concludes", "ready", ""),
        )
        late, _, _ = await _emit(db, _at(sample_event, "evt_push", 9), sample_router_output_engineer, None)
        card = await _card(db, late)
        assert card["status"] == "done"
        assert card["status_reason"] == "A later event concluded this work item."

    async def test_earlier_event_does_not_close_a_later_card(
        self, db, sample_event, sample_router_output_engineer,
    ):
        later, _, _ = await _emit(
            db, _at(sample_event, "evt_later", 12), sample_router_output_engineer,
            _verdict("reopens", "ready"),
        )
        await _emit(
            db, _at(sample_event, "evt_earlier", 9), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        assert (await _card(db, later))["status"] == "ready"

    async def test_pre_created_card_is_updated_in_place(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """The persona-worker path inserts a `pending` row first and emit updates
        it. The verdict applies to that row like to a fresh one."""
        await insert_test_event(db, sample_event.event_id)
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category, header, summary, status,
                entity_id, created_at, group_active_at)
               VALUES ('card_pre', ?, 'HIGH', 'ENGINEER', 'CODE', 'h', 's', 'pending', ?,
                       '2026-02-22 14:30:00', '2026-02-22 14:30:00')""",
            (sample_event.event_id, sample_event.entity_id),
        )
        await db.commit()

        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer,
            _verdict("concludes", "done", "Merged."), card_id="card_pre",
        )

        assert card_id == "card_pre"
        card = await _card(db, "card_pre")
        assert card["status"] == "done"
        assert card["status_reason"] == "Merged."
        assert card["resolved_at"] is not None
        assert card["previous_status"] == "ready"

    async def test_feature_switched_off(
        self, db, sample_event, sample_router_output_engineer,
    ):
        earlier, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        with patch("laya.pipeline.emit.llm_card_status_enabled", return_value=False):
            card_id, _, _ = await _emit(
                db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
                _verdict("concludes", "done"),
            )
        card = await _card(db, card_id)
        assert card["status"] == "ready"
        assert card["status_effect"] is None
        assert (await _card(db, earlier))["status"] == "ready"


# ---------------------------------------------------------------------------
# What the user did by hand is kept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUserActions:
    async def _client(self):
        from laya.main import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_reopen_returns_a_card_laya_closed_to_ready(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done", "Merged."),
        )
        async with await self._client() as client:
            resp = await client.post(f"/cards/{card_id}/reopen")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"
        card = await _card(db, card_id)
        assert card["status"] == "ready"
        assert card["status_set_by"] == "user"
        assert card["status_reason"] is None
        assert card["resolved_at"] is None

    async def test_reopened_card_is_not_closed_again(
        self, db, sample_event, sample_router_output_engineer,
    ):
        kept, _, _ = await _emit(
            db, _at(sample_event, "evt_a", 9), sample_router_output_engineer, _verdict("none", "ready"),
        )
        await _emit(
            db, _at(sample_event, "evt_b", 10), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        async with await self._client() as client:
            await client.post(f"/cards/{kept}/reopen")

        # While the user's reopened card is open, the work item counts as open.
        assert await work_item_state_before(
            db, sample_event.entity_id, "2026-02-22 11:00:00", "evt_none",
        ) == OPEN
        after, _, _ = await _emit(
            db, _at(sample_event, "evt_c", 11), sample_router_output_engineer, _verdict("none", "done"),
        )
        assert (await _card(db, after))["status"] == "ready"

        # A second conclusion closes the new card but not the one the user reopened.
        await _emit(
            db, _at(sample_event, "evt_d", 12), sample_router_output_engineer,
            _verdict("concludes", "done"),
        )
        assert (await _card(db, after))["status"] == "done"
        assert (await _card(db, kept))["status"] == "ready"

    async def test_reopen_of_a_card_the_user_closed_sets_no_marker(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(db, sample_event, sample_router_output_engineer, None)
        async with await self._client() as client:
            await client.post(f"/cards/{card_id}/done", json={})
            await client.post(f"/cards/{card_id}/reopen")

        card = await _card(db, card_id)
        assert card["status"] == "ready"
        assert card["status_set_by"] is None

    async def test_done_by_the_user_clears_the_marker(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done", "Merged."),
        )
        async with await self._client() as client:
            await client.post(f"/cards/{card_id}/reopen")
            resp = await client.post(f"/cards/{card_id}/done", json={})

        assert resp.status_code == 200
        card = await _card(db, card_id)
        assert card["status"] == "done"
        assert card["status_set_by"] is None
        assert card["status_reason"] is None
        assert card["read_at"] is not None

    async def test_reprocessing_a_reopened_card_keeps_it_open(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done"),
        )
        async with await self._client() as client:
            await client.post(f"/cards/{card_id}/reopen")

        # A reprocess sets the card to `pending` on the user's behalf and then
        # runs emit again for the same event and card.
        from laya.models.card_lifecycle import transition_card_status
        with patch("laya.models.card_lifecycle.manager.broadcast", new_callable=AsyncMock):
            await transition_card_status(
                card_id, "pending", actor="user", save_previous=False, allow_restore=True,
            )
        assert (await _card(db, card_id))["status_set_by"] == "user"
        await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done"),
            card_id=card_id,
        )
        card = await _card(db, card_id)
        assert card["status"] == "ready"
        assert card["status_set_by"] == "user"

    async def test_reopen_through_chat_sets_the_marker(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """The chat tools change status as actor "chat", which also stands for
        the user."""
        from laya.models.card_lifecycle import transition_card_status

        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done", "Merged."),
        )
        with patch("laya.models.card_lifecycle.manager.broadcast", new_callable=AsyncMock) as broadcast:
            await transition_card_status(card_id, "ready", actor="chat", allow_restore=True)

        card = await _card(db, card_id)
        assert card["status_set_by"] == "user"
        assert card["status_reason"] is None
        payload = broadcast.call_args.args[0]["payload"]
        assert payload == {"status": "ready", "status_set_by": "user", "status_reason": None}

    async def test_status_change_by_the_pipeline_leaves_the_marker(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """Only a person's change clears or sets the marker. An agent that moves
        a reopened card on does not."""
        from laya.models.card_lifecycle import transition_card_status

        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done"),
        )
        with patch("laya.models.card_lifecycle.manager.broadcast", new_callable=AsyncMock):
            await transition_card_status(card_id, "ready", actor="user", allow_restore=True)
            await transition_card_status(card_id, "agent_running", actor="agent")
            await transition_card_status(card_id, "done", actor="agent")

        assert (await _card(db, card_id))["status_set_by"] == "user"

    async def test_api_returns_reason_and_marker(
        self, db, sample_event, sample_router_output_engineer,
    ):
        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done", "Merged."),
        )
        async with await self._client() as client:
            resp = await client.get(f"/cards/{card_id}")

        data = resp.json()
        assert data["status"] == "done"
        assert data["status_reason"] == "Merged."
        assert data["status_set_by"] == "laya"


@pytest.mark.asyncio
class TestProcessingRuleOnClosedCard:
    async def test_set_status_on_a_card_laya_closed_is_skipped(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """done -> dismissed is not a valid transition. On a card that is already
        closed this is a skipped action, not a rule error."""
        from laya.models.processing_rules import SetStatusAction
        from laya.pipeline.processing_rules import _exec_set_status

        card_id, _, _ = await _emit(
            db, sample_event, sample_router_output_engineer, _verdict("concludes", "done"),
        )
        with patch("laya.models.card_lifecycle.manager.broadcast", new_callable=AsyncMock):
            result = await _exec_set_status(SetStatusAction(status="dismissed"), card_id)

        assert result == {"success": True, "skipped": True, "reason": "card is already done"}
        assert (await _card(db, card_id))["status"] == "done"

    async def test_invalid_transition_on_an_open_card_is_still_an_error(self, db):
        from laya.models.processing_rules import SetStatusAction
        from laya.pipeline.processing_rules import _exec_set_status
        from tests.conftest import insert_test_card

        await insert_test_card(db, status="executing")
        result = await _exec_set_status(SetStatusAction(status="dismissed"), "card_test")

        assert result["success"] is False


# ---------------------------------------------------------------------------
# The per-entity lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEntityStatusLock:
    async def test_lock_entry_is_removed_after_use(self):
        from laya.pipeline import card_status

        async with entity_status_lock("entity-x"):
            assert "entity-x" in card_status._entity_locks
        assert "entity-x" not in card_status._entity_locks

    async def test_second_caller_waits_for_the_first(self):
        import asyncio

        order: list[str] = []

        async def first():
            async with entity_status_lock("entity-y"):
                order.append("first in")
                await asyncio.sleep(0.01)
                order.append("first out")

        async def second():
            await asyncio.sleep(0)  # let `first` take the lock
            async with entity_status_lock("entity-y"):
                order.append("second in")

        await asyncio.gather(first(), second())
        assert order == ["first in", "first out", "second in"]
