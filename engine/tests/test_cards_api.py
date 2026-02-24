"""Tests for the Cards REST API."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _insert_test_card(db, card_id="card_test", event_id="evt_api_test", priority="HIGH",
                            persona="ENGINEER", status="pending"):
    """Insert a card with its parent event for testing."""
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, "2026-02-22T14:30:00Z", "jira", "issue_assigned",
         "ticket", "BUG-1234", "NPE Test", "{}"),
    )
    intelligence = json.dumps(["Finding 1", "Finding 2"])
    staged_output = json.dumps({"type": "code_fix", "content": "Add null check"})
    suggested_actions = json.dumps([
        {"action_id": "act_1", "label": "Post Comment", "action_type": "comment",
         "target_platform": "jira", "payload": {"body": "Fix found"}}
    ])
    await db.execute(
        "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
        "header, summary, intelligence, staged_output, suggested_actions, status, privacy_tier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, event_id, priority, persona, "CODE", "Test Card Header",
         "Test summary", intelligence, staged_output, suggested_actions, status, 2),
    )
    await db.commit()


@pytest.mark.asyncio
class TestCardsAPI:
    async def test_get_cards_empty(self, db_m4):
        """GET /cards returns empty list when no cards exist."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cards"] == []
        assert data["total"] == 0

    async def test_get_cards_with_data(self, db_m4):
        """GET /cards returns cards when they exist."""
        await _insert_test_card(db_m4, "card_1", "evt_1")
        await _insert_test_card(db_m4, "card_2", "evt_2", priority="CRITICAL")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["cards"]) == 2

    async def test_get_cards_filter_by_status(self, db_m4):
        """GET /cards?status=pending filters correctly."""
        await _insert_test_card(db_m4, "card_p", "evt_p", status="pending")
        await _insert_test_card(db_m4, "card_a", "evt_a", status="approved")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards?status=pending")

        data = resp.json()
        assert data["total"] == 1
        assert data["cards"][0]["card_id"] == "card_p"

    async def test_get_cards_filter_by_priority(self, db_m4):
        """GET /cards?priority=CRITICAL filters correctly."""
        await _insert_test_card(db_m4, "card_h", "evt_h", priority="HIGH")
        await _insert_test_card(db_m4, "card_c", "evt_c", priority="CRITICAL")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards?priority=CRITICAL")

        data = resp.json()
        assert data["total"] == 1
        assert data["cards"][0]["priority"] == "CRITICAL"

    async def test_get_card_detail(self, db_m4):
        """GET /cards/:card_id returns full card detail."""
        await _insert_test_card(db_m4)

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

    async def test_get_card_404(self, db_m4):
        """GET /cards/:card_id returns 404 for non-existent card."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/cards/card_nonexistent")

        assert resp.status_code == 404

    async def test_approve_card(self, db_m4):
        """POST /cards/:card_id/approve updates status to approved."""
        await _insert_test_card(db_m4)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/approve", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["card_id"] == "card_test"

        # Verify DB
        rows = await db_m4.execute_fetchall(
            "SELECT status, resolved_at FROM action_cards WHERE card_id = ?",
            ("card_test",),
        )
        assert rows[0]["status"] == "approved"
        assert rows[0]["resolved_at"] is not None

    async def test_approve_card_409_on_non_pending(self, db_m4):
        """POST /cards/:card_id/approve returns 409 if card is not pending."""
        await _insert_test_card(db_m4, status="approved")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/approve", json={})

        assert resp.status_code == 409

    async def test_dismiss_card(self, db_m4):
        """POST /cards/:card_id/dismiss stores feedback and updates status."""
        await _insert_test_card(db_m4)

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

        rows = await db_m4.execute_fetchall(
            "SELECT status, user_feedback, feedback_type FROM action_cards WHERE card_id = ?",
            ("card_test",),
        )
        assert rows[0]["status"] == "dismissed"
        assert rows[0]["user_feedback"] == "Not relevant"
        assert rows[0]["feedback_type"] == "irrelevant"

    async def test_dismiss_card_409_on_non_pending(self, db_m4):
        """POST /cards/:card_id/dismiss returns 409 if card is not pending."""
        await _insert_test_card(db_m4, status="dismissed")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/cards/card_test/dismiss", json={})

        assert resp.status_code == 409
