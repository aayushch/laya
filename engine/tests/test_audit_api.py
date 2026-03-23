"""Tests for the Audit Log REST API."""

import pytest
from httpx import ASGITransport, AsyncClient


async def _insert_audit_entry(db, log_id, step="route", success=True,
                              event_id=None, card_id=None,
                              model="anthropic/claude-haiku-4-5-20251001",
                              input_tokens=500, output_tokens=200, latency_ms=450):
    await db.execute(
        "INSERT INTO audit_log (log_id, event_id, card_id, step, model_used, "
        "input_tokens, output_tokens, latency_ms, success) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (log_id, event_id, card_id, step, model, input_tokens, output_tokens,
         latency_ms, success),
    )
    await db.commit()


@pytest.mark.asyncio
class TestAuditLogAPI:
    async def test_empty_audit_log(self, db):
        """GET /audit-log returns empty list when no entries exist."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit-log")

        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_with_data(self, db):
        """GET /audit-log returns entries when data exists."""
        await _insert_audit_entry(db, "audit_1")
        await _insert_audit_entry(db, "audit_2", step="stage")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit-log")

        data = resp.json()
        assert data["total"] == 2
        assert len(data["entries"]) == 2

    async def test_filter_by_step(self, db):
        """GET /audit-log?step=route filters by step."""
        await _insert_audit_entry(db, "audit_r", step="route")
        await _insert_audit_entry(db, "audit_s", step="stage")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit-log?step=route")

        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["step"] == "route"

    async def test_filter_by_success(self, db):
        """GET /audit-log?success=false filters by success status."""
        await _insert_audit_entry(db, "audit_ok", success=True)
        await _insert_audit_entry(db, "audit_fail", success=False)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit-log?success=false")

        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["success"] is False

    async def test_pagination(self, db):
        """GET /audit-log supports pagination with limit and offset."""
        for i in range(10):
            await _insert_audit_entry(db, f"audit_pg_{i}")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/audit-log?limit=3&offset=0")
            data1 = resp.json()

            resp = await client.get("/audit-log?limit=3&offset=3")
            data2 = resp.json()

        assert data1["total"] == 10
        assert len(data1["entries"]) == 3
        assert len(data2["entries"]) == 3
        # Entries should be different between pages
        ids1 = {e["log_id"] for e in data1["entries"]}
        ids2 = {e["log_id"] for e in data2["entries"]}
        assert ids1.isdisjoint(ids2)
