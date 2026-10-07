# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the single-card ``retry_card`` chat/MCP tool (issue #44).

The tool requeues ONE dead event by card id. It must never grow a bulk form:
on local-model setups a mass retry starves live ingestion, so "retry all"
stays a human action in Settings -> Audit.
"""

import json

import pytest

from tests.conftest import insert_test_card


async def _set_event_status(db, event_id: str, status: str, error: str | None = None):
    await db.execute(
        """UPDATE events
           SET processing_status = ?, processing_attempts = 3, last_error = ?
           WHERE event_id = ?""",
        (status, error, event_id),
    )
    await db.commit()


async def _event(db, event_id: str) -> dict:
    rows = await db.execute_fetchall(
        """SELECT processing_status, processing_attempts, last_error, manual_retries
           FROM events WHERE event_id = ?""",
        (event_id,),
    )
    return dict(rows[0])


@pytest.mark.asyncio
class TestRetryCardTool:
    async def test_retries_dead_event_in_place(self, db):
        """A card whose event is dead gets its event requeued; the card row is untouched."""
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_dead", event_id="evt_dead", status="failed")
        await _set_event_status(db, "evt_dead", "dead", "TimeoutError: LLM unreachable")

        result = await card_tools.retry_card("card_dead")

        assert result["success"] is True
        assert result["card_id"] == "card_dead"
        assert result["event_id"] == "evt_dead"
        assert result["previous_status"] == "failed"
        assert result["previous_error"] == "TimeoutError: LLM unreachable"

        ev = await _event(db, "evt_dead")
        assert ev["processing_status"] == "queued"
        assert ev["processing_attempts"] == 0
        assert ev["last_error"] is None
        assert ev["manual_retries"] == 1

        # The pipeline resets the card when it picks the event up; the tool itself
        # leaves the card alone so it reuses the same card_id (no duplicate).
        rows = await db.execute_fetchall(
            "SELECT status FROM action_cards WHERE card_id = 'card_dead'"
        )
        assert rows[0]["status"] == "failed"

    async def test_retry_is_audited(self, db):
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_audit", event_id="evt_audit", status="failed")
        await _set_event_status(db, "evt_audit", "dead", "boom")
        await card_tools.retry_card("card_audit")

        rows = await db.execute_fetchall(
            "SELECT * FROM audit_log WHERE step = 'lifecycle'"
        )
        assert len(rows) == 1
        meta = json.loads(rows[0]["metadata"])
        assert rows[0]["card_id"] == "card_audit"
        assert rows[0]["event_id"] == "evt_audit"
        assert meta["action"] == "retry"
        assert meta["source"] == "chat"
        assert meta["previous_status"] == "failed"

    async def test_unknown_card(self, db):
        from laya.llm.tools import card_tools

        result = await card_tools.retry_card("card_nope")
        assert "not found" in result["error"]

    async def test_completed_event_is_not_retried(self, db):
        """Nothing to retry when the event finished; the tool points at reopen_card."""
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_ok", event_id="evt_ok", status="ready")
        await _set_event_status(db, "evt_ok", "completed")

        result = await card_tools.retry_card("card_ok")

        assert "error" in result
        assert "reopen_card" in result["error"]
        assert (await _event(db, "evt_ok"))["processing_status"] == "completed"

    @pytest.mark.parametrize("status", ["queued", "retrying", "processing"])
    async def test_in_flight_event_is_not_double_queued(self, db, status):
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_busy", event_id="evt_busy", status="pending")
        await _set_event_status(db, "evt_busy", status)

        result = await card_tools.retry_card("card_busy")

        assert "already being processed" in result["error"]
        ev = await _event(db, "evt_busy")
        assert ev["processing_status"] == status
        assert ev["manual_retries"] == 0

    async def test_rejects_a_list_of_ids(self, db):
        """Single-card contract: a list is refused outright, not partially applied."""
        from laya.llm.tools import card_tools

        await insert_test_card(db, card_id="card_a", event_id="evt_a", status="failed")
        await insert_test_card(db, card_id="card_b", event_id="evt_b", status="failed")
        await _set_event_status(db, "evt_a", "dead")
        await _set_event_status(db, "evt_b", "dead")

        result = await card_tools.retry_card(["card_a", "card_b"])  # type: ignore[arg-type]

        assert "exactly one card_id" in result["error"]
        assert (await _event(db, "evt_a"))["processing_status"] == "dead"
        assert (await _event(db, "evt_b"))["processing_status"] == "dead"

    @pytest.mark.parametrize("bad", ["", "   ", "card_a,card_b", "card_a card_b"])
    async def test_rejects_separators_and_empty(self, db, bad):
        from laya.llm.tools import card_tools

        result = await card_tools.retry_card(bad)
        assert "exactly one card_id" in result["error"]

    async def test_dispatch_through_executor(self, db):
        """The executor registry routes retry_card to the handler."""
        from laya.llm.tools import executor

        await insert_test_card(db, card_id="card_exec", event_id="evt_exec", status="failed")
        await _set_event_status(db, "evt_exec", "dead")

        out = json.loads(await executor.execute_tool("retry_card", {"card_id": "card_exec"}))
        assert out["success"] is True
        assert (await _event(db, "evt_exec"))["processing_status"] == "queued"


class TestRetryCardContract:
    def test_schema_is_single_string_id_only(self):
        """Exactly one required string parameter; no array, no bulk flag."""
        from laya.llm.tools.definitions import get_all_tool_definitions

        fn = next(
            d["function"] for d in get_all_tool_definitions()
            if d["function"]["name"] == "retry_card"
        )
        params = fn["parameters"]
        assert list(params["properties"]) == ["card_id"]
        assert params["properties"]["card_id"]["type"] == "string"
        assert params["required"] == ["card_id"]

    def test_is_a_write_scope_tool(self):
        from laya.llm.tools.definitions import read_tool_names, write_tool_names
        from laya.mcp.scope import scope_of

        assert "retry_card" in write_tool_names()
        assert "retry_card" not in read_tool_names()
        assert scope_of("retry_card") == "write"

    def test_ships_in_plain_chat_turn(self):
        """Card-write tools always ship, so retry needs no keyword to be available."""
        from laya.llm.tools.definitions import select_tool_definitions

        names = {d["function"]["name"] for d in select_tool_definitions("retry that card")}
        assert "retry_card" in names
