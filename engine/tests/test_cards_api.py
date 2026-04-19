"""Tests for the Cards REST API."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card, insert_test_event


@pytest.mark.asyncio
class TestCardsAPI:
    async def test_get_cards_empty(self, db):
        """GET /cards returns empty list when no cards exist."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cards"] == []
        assert data["total"] == 0

    async def test_get_cards_with_data(self, db):
        """GET /cards returns cards when they exist."""
        await insert_test_card(db, "card_1", "evt_1")
        await insert_test_card(db, "card_2", "evt_2", priority="CRITICAL")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["cards"]) == 2

    async def test_get_cards_filter_by_status(self, db):
        """GET /cards?status=pending filters correctly."""
        await insert_test_card(db, "card_p", "evt_p", status="pending")
        await insert_test_card(db, "card_d", "evt_d", status="done")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards?status=pending")

        data = resp.json()
        assert data["total"] == 1
        assert data["cards"][0]["card_id"] == "card_p"

    async def test_get_cards_filter_by_priority(self, db):
        """GET /cards?priority=CRITICAL filters correctly."""
        await insert_test_card(db, "card_h", "evt_h", priority="HIGH")
        await insert_test_card(db, "card_c", "evt_c", priority="CRITICAL")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards?priority=CRITICAL")

        data = resp.json()
        assert data["total"] == 1
        assert data["cards"][0]["priority"] == "CRITICAL"

    async def test_get_card_detail(self, db):
        """GET /cards/:card_id returns full card detail."""
        await insert_test_card(db)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["card_id"] == "card_test"
        assert data["header"] == "Test Card Header"
        assert data["intelligence"] == ["Finding 1", "Finding 2"]
        assert data["staged_output"]["type"] == "code_fix"
        assert len(data["suggested_actions"]) == 1

    async def test_get_card_404(self, db):
        """GET /cards/:card_id returns 404 for non-existent card."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_nonexistent")

        assert resp.status_code == 404

    async def test_mark_card_done(self, db):
        """POST /cards/:card_id/done updates status to done."""
        await insert_test_card(db, status="pending")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/done", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["card_id"] == "card_test"

        # Verify DB
        rows = await db.execute_fetchall(
            "SELECT status, resolved_at FROM action_cards WHERE card_id = ?",
            ("card_test",),
        )
        assert rows[0]["status"] == "done"
        assert rows[0]["resolved_at"] is not None

    async def test_mark_card_done_from_ready(self, db):
        """POST /cards/:card_id/done works for cards in 'ready' status."""
        await insert_test_card(db, status="ready")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/done", json={})

        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    async def test_mark_card_done_409_on_terminal(self, db):
        """POST /cards/:card_id/done returns 409 if card is already done."""
        await insert_test_card(db, status="done")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/done", json={})

        assert resp.status_code == 409

    async def test_approve_agent(self, db):
        """POST /cards/:card_id/approve-agent transitions requires_approval to agent_running."""
        await insert_test_card(db, status="requires_approval")
        # Set agent_prompt so the endpoint doesn't 409
        await db.execute(
            "UPDATE action_cards SET agent_prompt = 'Fix the bug' WHERE card_id = 'card_test'"
        )
        await db.commit()

        from laya.main import app
        transport = ASGITransport(app=app)
        from unittest.mock import AsyncMock, patch
        with patch("laya.api.cards_api.cancel_sessions_for_card", new_callable=AsyncMock):
            with patch("laya.workers.engineer.run_engineer_from_prompt", new_callable=AsyncMock):
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/cards/card_test/approve-agent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "agent_running"
        assert data["card_id"] == "card_test"

    async def test_approve_agent_409_on_wrong_status(self, db):
        """POST /cards/:card_id/approve-agent returns 409 if card is not requires_approval."""
        await insert_test_card(db, status="pending")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/approve-agent")

        assert resp.status_code == 409

    async def test_dismiss_card(self, db):
        """POST /cards/:card_id/dismiss stores feedback and updates status."""
        await insert_test_card(db)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/cards/card_test/dismiss",
                json={"reason": "Not relevant", "feedback_type": "irrelevant"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dismissed"

        rows = await db.execute_fetchall(
            "SELECT status, user_feedback, feedback_type FROM action_cards WHERE card_id = ?",
            ("card_test",),
        )
        assert rows[0]["status"] == "dismissed"
        assert rows[0]["user_feedback"] == "Not relevant"
        assert rows[0]["feedback_type"] == "irrelevant"

    async def test_dismiss_card_409_on_terminal(self, db):
        """POST /cards/:card_id/dismiss returns 409 if card is in terminal state."""
        await insert_test_card(db, status="dismissed")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/dismiss", json={})

        assert resp.status_code == 409

    async def test_dismiss_from_agent_running_status(self, db):
        """POST /cards/:card_id/dismiss works for cards in 'agent_running' status."""
        await insert_test_card(db, status="agent_running")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/cards/card_test/dismiss",
                json={"reason": "Cancelled by user"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    async def test_dismiss_done_returns_409(self, db):
        """POST /cards/:card_id/dismiss returns 409 for done cards."""
        await insert_test_card(db, status="done")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/dismiss", json={})

        assert resp.status_code == 409

    async def test_dismiss_failed_returns_409(self, db):
        """POST /cards/:card_id/dismiss returns 409 for failed cards."""
        await insert_test_card(db, status="failed")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/dismiss", json={})

        assert resp.status_code == 409


@pytest.mark.asyncio
class TestActionPayloadPolish:
    async def test_polish_flips_flags_and_writes_polished_text(self, db):
        """POST /cards/:id/action-payload/polish writes polished text back to the action."""
        await insert_test_card(db, status="ready")

        fake_response = type("R", (), {"content": "Polished reply body."})()
        from laya.main import app

        with patch("laya.llm.client.llm_call", new=AsyncMock(return_value=fake_response)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/cards/card_test/action-payload/polish",
                    json={"action_id": "act_1"},
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "polishing"

                # Drain background tasks so the polish finishes before we read DB
                await asyncio.sleep(0)
                for _ in range(20):
                    pending = [t for t in asyncio.all_tasks() if t.get_name().startswith("polish_")]
                    if not pending:
                        break
                    await asyncio.gather(*pending, return_exceptions=True)

        rows = await db.execute_fetchall(
            "SELECT suggested_actions FROM action_cards WHERE card_id = ?", ("card_test",)
        )
        actions = json.loads(rows[0]["suggested_actions"])
        payload = actions[0]["payload"]
        assert payload["body"] == "Polished reply body."
        assert payload["_polishing"] is False
        assert "_polished_at" in payload

    async def test_polish_conflict_when_already_polishing(self, db):
        """A second polish request returns 409 while one is in flight."""
        await insert_test_card(db, status="ready")
        # Seed the action as already polishing
        rows = await db.execute_fetchall(
            "SELECT suggested_actions FROM action_cards WHERE card_id = ?", ("card_test",)
        )
        actions = json.loads(rows[0]["suggested_actions"])
        actions[0]["payload"]["_polishing"] = True
        await db.execute(
            "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
            (json.dumps(actions), "card_test"),
        )
        await db.commit()

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/cards/card_test/action-payload/polish",
                json={"action_id": "act_1"},
            )

        assert resp.status_code == 409

    async def test_polish_unknown_action_returns_404(self, db):
        await insert_test_card(db, status="ready")
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/cards/card_test/action-payload/polish",
                json={"action_id": "does_not_exist"},
            )
        assert resp.status_code == 404

    async def test_update_action_payload_sets_edited_flag(self, db):
        """The existing update endpoint flips `_edited: true` so the Polish link can appear."""
        await insert_test_card(db, status="ready")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/cards/card_test/action-payload",
                json={"action_id": "act_1", "payload": {"body": "User edit"}},
            )
            assert resp.status_code == 200

        rows = await db.execute_fetchall(
            "SELECT suggested_actions FROM action_cards WHERE card_id = ?", ("card_test",)
        )
        payload = json.loads(rows[0]["suggested_actions"])[0]["payload"]
        assert payload["body"] == "User edit"
        assert payload["_edited"] is True
