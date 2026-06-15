# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Audit/Settings red-dot indicator:

  - GET /audit/failure-summary (startup seed counts)
  - clear-down semantics (counts drop to zero when failures are resolved)
  - `audit_failure` WebSocket broadcasts on new dead events + ingestion failures
"""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_event


async def _make_dead(db, event_id: str):
    await db.execute(
        """UPDATE events
           SET processing_status = 'dead', processing_attempts = 3,
               last_error = 'boom'
           WHERE event_id = ?""",
        (event_id,),
    )
    await db.commit()


def _ingestion_payload(workflow_id: str = "wf_test", message: str = "API rate limit"):
    return {
        "workflow_id": workflow_id,
        "error_message": message,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "node_name": "HTTP Request",
        "error_name": "NodeApiError",
        "platform": "jira",
    }


@pytest.mark.asyncio
class TestFailureSummary:
    async def test_empty(self, db):
        """No failures → all zero, has_failures false."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit/failure-summary")

        assert resp.status_code == 200
        assert resp.json() == {
            "dead_events": 0,
            "ingestion_errors": 0,
            "has_failures": False,
        }

    async def test_counts_dead_events(self, db):
        """Dead events are counted; non-dead are not."""
        await insert_test_event(db, "evt_ok")
        await insert_test_event(db, "evt_dead1")
        await insert_test_event(db, "evt_dead2")
        await _make_dead(db, "evt_dead1")
        await _make_dead(db, "evt_dead2")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = (await client.get("/audit/failure-summary")).json()

        assert data["dead_events"] == 2
        assert data["ingestion_errors"] == 0
        assert data["has_failures"] is True

    async def test_counts_uncleared_ingestion_errors(self, db):
        """Ingestion errors count while uncleared and stop counting once cleared.

        This is the API-level proof that the red dot disappears when the user
        clears the failures."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post = await client.post("/ingestion-errors", json=_ingestion_payload())
            assert post.status_code == 202
            error_id = post.json()["error_id"]

            data = (await client.get("/audit/failure-summary")).json()
            assert data["ingestion_errors"] == 1
            assert data["has_failures"] is True

            # Clear it → count returns to zero, dot would disappear.
            await client.post(f"/ingestion-errors/{error_id}/clear")
            data = (await client.get("/audit/failure-summary")).json()
            assert data["ingestion_errors"] == 0
            assert data["has_failures"] is False

    async def test_clear_down_dead_events(self, db):
        """Retrying all dead events drops the count to zero."""
        await insert_test_event(db, "evt_d1")
        await _make_dead(db, "evt_d1")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/audit/failure-summary")).json()["has_failures"] is True
            await client.post("/events/dead/retry", json={"all": True})
            assert (await client.get("/audit/failure-summary")).json()["has_failures"] is False


@pytest.mark.asyncio
class TestAuditFailureBroadcast:
    async def test_ingestion_error_broadcasts(self, db, monkeypatch):
        """POST /ingestion-errors pushes an `audit_failure` WS message with counts."""
        sent: list[dict] = []

        async def fake_broadcast(message):
            sent.append(message)

        monkeypatch.setattr("laya.api.websocket.manager.broadcast", fake_broadcast)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/ingestion-errors", json=_ingestion_payload())

        assert len(sent) == 1
        assert sent[0]["type"] == "audit_failure"
        assert sent[0]["payload"]["ingestion_errors"] == 1
        assert sent[0]["payload"]["kind"] == "ingestion_error"

    async def test_mark_failed_broadcasts_when_dead(self, db, monkeypatch):
        """_mark_failed broadcasts only when the event becomes permanently dead."""
        sent: list[dict] = []

        async def fake_broadcast(message):
            sent.append(message)

        monkeypatch.setattr("laya.api.websocket.manager.broadcast", fake_broadcast)

        from laya.pipeline.queue import _mark_failed

        # Under the retry ceiling → no broadcast (still retrying).
        await insert_test_event(db, "evt_retry")
        await _mark_failed("evt_retry", "transient")
        assert sent == []

        # Past the ceiling → broadcast with kind=dead_event.
        await insert_test_event(db, "evt_dead")
        await db.execute(
            "UPDATE events SET processing_attempts = 99 WHERE event_id = 'evt_dead'"
        )
        await db.commit()
        await _mark_failed("evt_dead", "fatal")

        assert len(sent) == 1
        assert sent[0]["type"] == "audit_failure"
        assert sent[0]["payload"]["kind"] == "dead_event"
        assert sent[0]["payload"]["dead_events"] == 1
