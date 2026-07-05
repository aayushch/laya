# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared-connection invariant guard (db/sqlite.transaction — P4-12)."""

import pytest

from laya.db.sqlite import transaction
from tests.conftest import insert_test_card


class TestTransaction:
    @pytest.mark.asyncio
    async def test_commits_on_success(self, db):
        """A guarded block's writes are committed on normal exit."""
        async with transaction() as tdb:
            await tdb.execute(
                "INSERT INTO spaces (space_id, name) VALUES (?, ?)",
                ("sp_commit", "Committed"),
            )
        rows = await db.execute_fetchall(
            "SELECT name FROM spaces WHERE space_id = ?", ("sp_commit",)
        )
        assert len(rows) == 1 and rows[0]["name"] == "Committed"

    @pytest.mark.asyncio
    async def test_rolls_back_on_exception(self, db):
        """A raising block rolls back its own uncommitted writes and re-raises."""
        with pytest.raises(RuntimeError, match="boom"):
            async with transaction() as tdb:
                await tdb.execute(
                    "INSERT INTO spaces (space_id, name) VALUES (?, ?)",
                    ("sp_rollback", "ShouldVanish"),
                )
                raise RuntimeError("boom")
        rows = await db.execute_fetchall(
            "SELECT 1 FROM spaces WHERE space_id = ?", ("sp_rollback",)
        )
        assert rows == []  # write was undone, not flushed by a later commit

    @pytest.mark.asyncio
    async def test_yields_shared_connection(self, db):
        """transaction() yields the one shared connection (get_db()), not a copy."""
        async with transaction() as tdb:
            assert tdb is db


class TestDeleteCardCascade:
    @pytest.mark.asyncio
    async def test_cascade_removes_card_and_tag_assignments(self, db):
        """_delete_card_cascade clears the card row and its polymorphic tags."""
        from laya.api.cards_api import _delete_card_cascade

        await insert_test_card(db, card_id="card_del", event_id="evt_del")
        # Attach a tag assignment (no FK to action_cards — must be cleaned explicitly)
        cur = await db.execute(
            "INSERT INTO tags (name, color) VALUES (?, ?)", ("urgent", "#f00")
        )
        tag_id = cur.lastrowid
        await db.execute(
            "INSERT INTO tag_assignments (tag_id, target_type, target_id) VALUES (?, 'card', ?)",
            (tag_id, "card_del"),
        )
        await db.commit()

        await _delete_card_cascade(db, "card_del", "evt_del")

        card_rows = await db.execute_fetchall(
            "SELECT 1 FROM action_cards WHERE card_id = ?", ("card_del",)
        )
        tag_rows = await db.execute_fetchall(
            "SELECT 1 FROM tag_assignments WHERE target_type = 'card' AND target_id = ?",
            ("card_del",),
        )
        assert card_rows == []
        assert tag_rows == []  # orphan tag assignment cleaned by the cascade

    @pytest.mark.asyncio
    async def test_cascade_keeps_event_shared_by_another_card(self, db):
        """The source event survives if another card still references it."""
        from laya.api.cards_api import _delete_card_cascade

        await insert_test_card(db, card_id="card_a", event_id="evt_shared")
        await insert_test_card(db, card_id="card_b", event_id="evt_shared")

        await _delete_card_cascade(db, "card_a", "evt_shared")

        ev_rows = await db.execute_fetchall(
            "SELECT 1 FROM events WHERE event_id = ?", ("evt_shared",)
        )
        assert len(ev_rows) == 1  # still referenced by card_b, not deleted
