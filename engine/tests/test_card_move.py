# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for POST /cards/{card_id}/move — moving a card (and its whole group)
to another space.

The unit of a move is the GROUP: a card in a multi-card entity group moves the
whole entity group; a card in a context group moves every entity group in it; a
truly standalone card moves alone. Only the cheap, correctness-critical copies of
space_id are synced (card + event columns, group_summary label, omni_queue rows,
ChromaDB metadata); rollups (Omni/daily summaries) are intentionally left stale.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card


async def _add_space(db, space_id="sp_work", name="Work"):
    await db.execute(
        "INSERT INTO spaces (space_id, name) VALUES (?, ?)", (space_id, name)
    )
    await db.commit()


async def _move(card_id, space_id, dry_run=False):
    from laya.main import app
    transport = ASGITransport(app=app)
    # ChromaDB metadata sync is best-effort and off-path here — stub it so the test
    # doesn't spin up a real collection.
    with patch("laya.db.chromadb_store.update_document_metadata", new=AsyncMock()):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/cards/{card_id}/move",
                json={"space_id": space_id, "dry_run": dry_run},
            )


@pytest.mark.asyncio
class TestMoveCard:
    async def test_unknown_target_space_404(self, db):
        await insert_test_card(db, "card_1", "evt_1", space_id="default")
        resp = await _move("card_1", "nope")
        assert resp.status_code == 404

    async def test_unknown_card_404(self, db):
        await _add_space(db)
        resp = await _move("card_missing", "sp_work")
        assert resp.status_code == 404

    async def test_same_space_is_noop(self, db):
        await _add_space(db)
        await insert_test_card(db, "card_1", "evt_1", space_id="sp_work",
                               entity_id="jira:ticket:SOLO-1")
        resp = await _move("card_1", "sp_work")
        assert resp.status_code == 200
        assert resp.json()["status"] == "unchanged"

    async def test_standalone_card_moves_alone(self, db):
        await _add_space(db)
        # Distinct entity_id and no siblings -> standalone.
        await insert_test_card(db, "card_solo", "evt_solo", space_id="default",
                               entity_id="jira:ticket:SOLO-9")
        resp = await _move("card_solo", "sp_work")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "moved"
        assert body["scope"] == "standalone"
        assert body["count"] == 1

        card_rows = await db.execute_fetchall(
            "SELECT space_id FROM action_cards WHERE card_id = 'card_solo'"
        )
        assert card_rows[0]["space_id"] == "sp_work"
        # The underlying event follows the card.
        evt_rows = await db.execute_fetchall(
            "SELECT space_id FROM events WHERE event_id = 'evt_solo'"
        )
        assert evt_rows[0]["space_id"] == "sp_work"

    async def test_entity_group_moves_as_a_unit(self, db):
        await _add_space(db)
        # Two cards sharing the default entity_id form a multi-card entity group.
        await insert_test_card(db, "card_a", "evt_a", space_id="default")
        await insert_test_card(db, "card_b", "evt_b", space_id="default")
        # A group_summary row keyed by that entity, tagged with the old space.
        await db.execute(
            "INSERT INTO group_summaries (entity_id, headline, summary, card_ids, "
            "card_count, space_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("jira:ticket:BUG-1234", "H", "S", "[]", 2, "default"),
        )
        await db.commit()

        resp = await _move("card_a", "sp_work")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "entity"
        assert body["count"] == 2
        assert set(body["moved_card_ids"]) == {"card_a", "card_b"}

        # BOTH cards moved, not just the clicked one.
        rows = await db.execute_fetchall(
            "SELECT card_id, space_id FROM action_cards WHERE card_id IN ('card_a','card_b')"
        )
        assert all(r["space_id"] == "sp_work" for r in rows)
        # The group_summary label followed (membership unchanged, no regeneration).
        gs = await db.execute_fetchall(
            "SELECT space_id FROM group_summaries WHERE entity_id = 'jira:ticket:BUG-1234'"
        )
        assert gs[0]["space_id"] == "sp_work"

    async def test_context_group_moves_every_entity(self, db):
        await _add_space(db)
        # Two DIFFERENT entity groups linked by a shared context_id.
        await insert_test_card(db, "card_x", "evt_x", space_id="default",
                               entity_id="jira:ticket:AAA-1")
        await insert_test_card(db, "card_y", "evt_y", space_id="default",
                               entity_id="slack:thread:BBB-2")
        await db.execute(
            "UPDATE action_cards SET context_id = 'ctx_1' WHERE card_id IN ('card_x','card_y')"
        )
        await db.commit()

        resp = await _move("card_x", "sp_work")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "context"
        assert body["count"] == 2
        assert set(body["moved_card_ids"]) == {"card_x", "card_y"}

        rows = await db.execute_fetchall(
            "SELECT space_id FROM action_cards WHERE card_id IN ('card_x','card_y')"
        )
        assert all(r["space_id"] == "sp_work" for r in rows)

    async def test_dry_run_previews_without_mutating(self, db):
        await _add_space(db)
        await insert_test_card(db, "card_a", "evt_a", space_id="default")
        await insert_test_card(db, "card_b", "evt_b", space_id="default")

        resp = await _move("card_a", "sp_work", dry_run=True)
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "entity"
        assert body["card_count"] == 2
        assert "warning" in body and body["space_name"] == "Work"
        # Omni-staleness note is surfaced to the user.
        assert "resynthesis" in body["warning"].lower()

        # Nothing moved.
        rows = await db.execute_fetchall(
            "SELECT space_id FROM action_cards WHERE card_id IN ('card_a','card_b')"
        )
        assert all(r["space_id"] == "default" for r in rows)
