# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the card Reprocess endpoint (re-run the pipeline on a card's event
in place, clearing the cached classification and skipping summary rollups)."""

import pytest
from fastapi import HTTPException

from laya.api.cards_lifecycle import reprocess_card
from laya.pipeline import emit as emit_mod
from tests.conftest import insert_test_card


@pytest.mark.asyncio
class TestReprocessCard:
    async def _row(self, db, sql, args):
        rows = await db.execute_fetchall(sql, args)
        return rows[0] if rows else None

    async def test_reenqueues_clears_router_and_flags_skip(self, db):
        emit_mod._reprocess_event_ids.discard("evt_rp")
        await insert_test_card(db, card_id="card_rp", event_id="evt_rp", status="ready")
        # Simulate a prior classification cached on the event.
        await db.execute(
            "UPDATE events SET router_output = ?, processing_status = 'completed' WHERE event_id = ?",
            ('{"persona": "ENGINEER"}', "evt_rp"),
        )
        await db.commit()

        result = await reprocess_card("card_rp")
        assert result == {"status": "reprocessing", "card_id": "card_rp"}

        ev = await self._row(db, "SELECT router_output, processing_status FROM events WHERE event_id = ?", ("evt_rp",))
        assert ev["router_output"] is None            # fresh classification forced
        assert ev["processing_status"] == "queued"    # re-enqueued for the pipeline

        card = await self._row(db, "SELECT status FROM action_cards WHERE card_id = ?", ("card_rp",))
        assert card["status"] == "pending"            # UI shows it reprocessing

        # The imminent re-emit will skip the summary rollups.
        assert emit_mod._skip_summaries("evt_rp") is True

    async def test_missing_card_404(self, db):
        with pytest.raises(HTTPException) as ei:
            await reprocess_card("nope")
        assert ei.value.status_code == 404

    async def test_already_processing_409(self, db):
        await insert_test_card(db, card_id="card_busy", event_id="evt_busy", status="agent_running")
        with pytest.raises(HTTPException) as ei:
            await reprocess_card("card_busy")
        assert ei.value.status_code == 409

    async def test_missing_raw_event_409(self, db):
        await insert_test_card(db, card_id="card_norraw", event_id="evt_noraw", status="ready")
        await db.execute("UPDATE events SET raw_json = '' WHERE event_id = ?", ("evt_noraw",))
        await db.commit()
        with pytest.raises(HTTPException) as ei:
            await reprocess_card("card_norraw")
        assert ei.value.status_code == 409

    async def test_emit_skip_flag_is_one_shot_semantics(self, db):
        # mark_reprocess adds; _skip_summaries reflects membership; discard removes.
        emit_mod._reprocess_event_ids.discard("evt_flag")
        assert emit_mod._skip_summaries("evt_flag") is False
        emit_mod.mark_reprocess("evt_flag")
        assert emit_mod._skip_summaries("evt_flag") is True
        emit_mod._reprocess_event_ids.discard("evt_flag")
        assert emit_mod._skip_summaries("evt_flag") is False
