# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the filtered events list + export (informational Audit section)."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_event


async def _make_filtered(db, event_id: str, rule: str = "Ignore bots", created_at: str | None = None):
    """Mark an existing event as filtered, optionally back-dating created_at."""
    if created_at is not None:
        await db.execute(
            "UPDATE events SET processing_status = 'filtered', filtered = TRUE, "
            "filter_rule = ?, created_at = ? WHERE event_id = ?",
            (rule, created_at, event_id),
        )
    else:
        await db.execute(
            "UPDATE events SET processing_status = 'filtered', filtered = TRUE, "
            "filter_rule = ? WHERE event_id = ?",
            (rule, event_id),
        )
    await db.commit()


@pytest.mark.asyncio
class TestFilteredEventsList:
    async def test_list_empty(self, db):
        """GET /events/filtered returns empty when no filtered events exist."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/filtered")

        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["total"] == 0

    async def test_list_returns_filtered_only(self, db):
        """Only events with processing_status='filtered' are listed."""
        await insert_test_event(db, "evt_f1", subject_title="Bot noise")
        await _make_filtered(db, "evt_f1", rule="Ignore bots")
        await insert_test_event(db, "evt_normal", subject_title="Real ticket")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/filtered")

        data = resp.json()
        assert data["total"] == 1
        evt = data["events"][0]
        assert evt["event_id"] == "evt_f1"
        assert evt["filter_rule"] == "Ignore bots"


@pytest.mark.asyncio
class TestFilteredEventsExport:
    async def test_export_all(self, db):
        """Export returns every filtered event with envelope metadata."""
        await insert_test_event(db, "evt_f1")
        await _make_filtered(db, "evt_f1")
        await insert_test_event(db, "evt_f2", subject_id="BUG-2", subject_title="More noise")
        await _make_filtered(db, "evt_f2", rule="Drop low priority")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/filtered/export")

        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "filtered_events"
        assert data["days"] == 0
        assert data["since"] is None
        assert data["count"] == 2
        assert len(data["events"]) == 2
        # Richer columns than the list view are present.
        assert "actor_email" in data["events"][0]
        assert "filter_rule" in data["events"][0]

    async def test_export_timeframe_cutoff(self, db):
        """days=N drops events filtered before the cutoff; days=0 keeps all."""
        await insert_test_event(db, "evt_recent")
        await _make_filtered(db, "evt_recent")  # created_at defaults to now
        await insert_test_event(db, "evt_old", subject_id="BUG-OLD")
        await _make_filtered(db, "evt_old", created_at="2020-01-01 00:00:00")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/events/filtered/export?days=7")
            windowed = resp.json()
            resp = await client.get("/events/filtered/export")
            all_time = resp.json()

        assert windowed["count"] == 1
        assert windowed["since"] is not None
        assert windowed["events"][0]["event_id"] == "evt_recent"
        assert all_time["count"] == 2
