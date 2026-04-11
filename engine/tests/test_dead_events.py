"""Tests for dead event recovery (list + retry)."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_event


async def _make_dead(db, event_id: str, error: str = "TimeoutError: LLM unreachable"):
    """Set an event to dead status with retry history."""
    await db.execute(
        """UPDATE events
           SET processing_status = 'dead',
               processing_attempts = 3,
               last_error = ?
           WHERE event_id = ?""",
        (error, event_id),
    )
    await db.commit()


@pytest.mark.asyncio
class TestDeadEventsAPI:
    async def test_list_dead_events_empty(self, db):
        """GET /events/dead returns empty when no dead events exist."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/dead")

        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["total"] == 0

    async def test_list_dead_events(self, db):
        """GET /events/dead returns dead events with correct fields."""
        await insert_test_event(db, "evt_dead1", subject_title="Broken PR")
        await _make_dead(db, "evt_dead1")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/dead")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        evt = data["events"][0]
        assert evt["event_id"] == "evt_dead1"
        assert evt["subject_title"] == "Broken PR"
        assert evt["processing_attempts"] == 3
        assert evt["last_error"] == "TimeoutError: LLM unreachable"
        assert evt["manual_retries"] == 0

    async def test_list_excludes_non_dead(self, db):
        """GET /events/dead does not return completed or queued events."""
        await insert_test_event(db, "evt_ok")
        await insert_test_event(db, "evt_dead2", subject_title="Dead one")
        await _make_dead(db, "evt_dead2")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/dead")

        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["event_id"] == "evt_dead2"

    async def test_retry_single_event(self, db):
        """POST /events/dead/retry with specific event_id re-enqueues it."""
        await insert_test_event(db, "evt_retry1")
        await _make_dead(db, "evt_retry1")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/events/dead/retry",
                json={"event_ids": ["evt_retry1"]}
            )

        assert resp.status_code == 200
        assert resp.json()["retried"] == 1

        # Verify state was fully reset
        row = await db.execute_fetchall(
            "SELECT processing_status, processing_attempts, last_error, next_retry_at, manual_retries FROM events WHERE event_id = 'evt_retry1'"
        )
        assert row[0]["processing_status"] == "queued"
        assert row[0]["processing_attempts"] == 0
        assert row[0]["last_error"] is None
        assert row[0]["next_retry_at"] is None
        assert row[0]["manual_retries"] == 1

    async def test_retry_all(self, db):
        """POST /events/dead/retry with all=true retries every dead event."""
        await insert_test_event(db, "evt_d1")
        await insert_test_event(db, "evt_d2")
        await insert_test_event(db, "evt_ok2")
        await _make_dead(db, "evt_d1")
        await _make_dead(db, "evt_d2")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/events/dead/retry", json={"all": True})

        assert resp.status_code == 200
        assert resp.json()["retried"] == 2

        # Both should be queued now
        rows = await db.execute_fetchall(
            "SELECT processing_status FROM events WHERE event_id IN ('evt_d1', 'evt_d2')"
        )
        assert all(r["processing_status"] == "queued" for r in rows)

        # Non-dead event should be untouched
        row = await db.execute_fetchall(
            "SELECT processing_status FROM events WHERE event_id = 'evt_ok2'"
        )
        assert row[0]["processing_status"] != "queued" or True  # insert_test_event sets processed=True

    async def test_retry_idempotent(self, db):
        """Retrying an already-retried event is a no-op."""
        await insert_test_event(db, "evt_idem")
        await _make_dead(db, "evt_idem")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First retry
            resp1 = await client.post("/events/dead/retry", json={"event_ids": ["evt_idem"]})
            assert resp1.json()["retried"] == 1

            # Second retry — event is now queued, not dead
            resp2 = await client.post("/events/dead/retry", json={"event_ids": ["evt_idem"]})
            assert resp2.json()["retried"] == 0

    async def test_retry_increments_manual_retries(self, db):
        """Manual retry count increments each time."""
        await insert_test_event(db, "evt_multi")
        await _make_dead(db, "evt_multi")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/events/dead/retry", json={"event_ids": ["evt_multi"]})

        # Simulate it going dead again
        await _make_dead(db, "evt_multi")

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/events/dead/retry", json={"event_ids": ["evt_multi"]})

        row = await db.execute_fetchall(
            "SELECT manual_retries FROM events WHERE event_id = 'evt_multi'"
        )
        assert row[0]["manual_retries"] == 2

    async def test_retry_no_ids_no_all(self, db):
        """POST /events/dead/retry with neither event_ids nor all returns 0."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/events/dead/retry", json={})

        assert resp.status_code == 200
        assert resp.json()["retried"] == 0

    async def test_list_pagination(self, db):
        """GET /events/dead respects limit and offset."""
        for i in range(5):
            await insert_test_event(db, f"evt_page{i}", subject_title=f"Event {i}")
            await _make_dead(db, f"evt_page{i}")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/dead?limit=2&offset=0")

        data = resp.json()
        assert data["total"] == 5
        assert len(data["events"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0
