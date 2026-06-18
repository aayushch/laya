# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the context rules REST API (/context-rules)."""

import pytest
from httpx import ASGITransport, AsyncClient


async def _insert_rule(db, rule_text, *, source="learned", active=1, space_id=None):
    await db.execute(
        """INSERT INTO context_rules (space_id, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (space_id, rule_text, source, active),
    )
    await db.commit()


async def _client():
    from laya.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestContextRulesAPI:
    async def test_empty(self, db):
        async with await _client() as client:
            resp = await client.get("/context-rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules"] == []
        assert data["total"] == 0

    async def test_create_is_manual(self, db):
        async with await _client() as client:
            resp = await client.post("/context-rules", json={"rule_text": "group X with Y"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "created"

            listed = (await client.get("/context-rules")).json()
        assert listed["total"] == 1
        rule = listed["rules"][0]
        assert rule["rule_text"] == "group X with Y"
        assert rule["source"] == "manual"
        assert rule["active"] is True

    async def test_create_rejects_blank(self, db):
        async with await _client() as client:
            resp = await client.post("/context-rules", json={"rule_text": "   "})
        assert resp.status_code == 400

    async def test_update_text_and_active(self, db):
        await _insert_rule(db, "original")
        async with await _client() as client:
            rid = (await client.get("/context-rules")).json()["rules"][0]["id"]
            r = await client.put(f"/context-rules/{rid}", json={"rule_text": "updated", "active": False})
            assert r.status_code == 200
            rule = (await client.get("/context-rules")).json()["rules"][0]
        assert rule["rule_text"] == "updated"
        assert rule["active"] is False

    async def test_delete_then_404(self, db):
        await _insert_rule(db, "to delete")
        async with await _client() as client:
            rid = (await client.get("/context-rules")).json()["rules"][0]["id"]
            assert (await client.delete(f"/context-rules/{rid}")).status_code == 200
            assert (await client.get("/context-rules")).json()["total"] == 0
            # second delete / update should 404
            assert (await client.delete(f"/context-rules/{rid}")).status_code == 404
            assert (await client.put(f"/context-rules/{rid}", json={"active": False})).status_code == 404

    async def test_filter_by_source_and_active(self, db):
        await _insert_rule(db, "learned active", source="learned", active=1)
        await _insert_rule(db, "manual inactive", source="manual", active=0)
        async with await _client() as client:
            learned = (await client.get("/context-rules?source=learned")).json()
            manual = (await client.get("/context-rules?source=manual")).json()
            inactive = (await client.get("/context-rules?active=false")).json()
        assert learned["total"] == 1 and learned["rules"][0]["source"] == "learned"
        assert manual["total"] == 1 and manual["rules"][0]["source"] == "manual"
        assert inactive["total"] == 1 and inactive["rules"][0]["rule_text"] == "manual inactive"

    async def test_pagination(self, db):
        for i in range(5):
            await _insert_rule(db, f"rule {i}")
        async with await _client() as client:
            page1 = (await client.get("/context-rules?limit=2&offset=0")).json()
            page3 = (await client.get("/context-rules?limit=2&offset=4")).json()
        assert page1["total"] == 5
        assert len(page1["rules"]) == 2
        assert len(page3["rules"]) == 1
