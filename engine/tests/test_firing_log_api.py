# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the processing-rule firing log REST API (GET /processing-rules/firings)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card


async def _insert_rule(db, rule_id=1, name="Test Rule"):
    await db.execute(
        "INSERT INTO processing_rules (id, name, condition_json, actions_json) "
        "VALUES (?, ?, ?, ?)",
        (rule_id, name, '{"field": "event.source.platform", "operator": "exists"}', "[]"),
    )
    await db.commit()


async def _insert_firing(db, firing_id, rule_id, card_id, results, *,
                         actions=None, error=None, event_id="evt_test", fired_at=None):
    cols = ["id", "rule_id", "card_id", "entity_id", "event_id",
            "actions_json", "results_json", "error"]
    vals = [firing_id, rule_id, card_id, None, event_id,
            json.dumps(actions if actions is not None else [{"type": "set_status"}]),
            json.dumps(results), error]
    if fired_at is not None:
        cols.append("fired_at")
        vals.append(fired_at)
    placeholders = ",".join("?" for _ in vals)
    await db.execute(
        f"INSERT INTO processing_rule_firings ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    await db.commit()


async def _get(path):
    from laya.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
class TestFiringLogAPI:
    async def test_empty(self, db):
        """Returns empty list (and 200, proving route is not coerced to {rule_id})."""
        resp = await _get("/processing-rules/firings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_route_not_shadowed_by_rule_id(self, db):
        """The literal /firings path must resolve here, not 422 against {rule_id:int}."""
        resp = await _get("/processing-rules/firings")
        assert resp.status_code == 200  # not 422

    async def test_derived_outcomes(self, db):
        """success / error / skipped outcomes are derived from results_json + error."""
        await _insert_rule(db)
        await insert_test_card(db, card_id="card_test")
        # success
        await _insert_firing(db, 1, 1, "card_test", [{"success": True, "status": "done"}])
        # error via error column
        await _insert_firing(db, 2, 1, "card_test", [{"success": False, "error": "boom"}], error="boom")
        # error via a NON-last failed result (error column is NULL) — must still be "error"
        await _insert_firing(db, 3, 1, "card_test",
                             [{"success": False, "error": "x"}, {"success": True}])
        # skipped
        await _insert_firing(db, 4, 1, "card_test",
                             [{"success": True, "skipped": True, "reason": "agent already running"}])

        data = (await _get("/processing-rules/firings")).json()
        by_id = {e["id"]: e for e in data["entries"]}
        assert data["total"] == 4
        assert by_id[1]["outcome"] == "success"
        assert by_id[2]["outcome"] == "error"
        assert by_id[3]["outcome"] == "error"
        assert by_id[4]["outcome"] == "skipped"
        assert by_id[4]["skip_reason"] == "agent already running"

    async def test_filter_by_outcome(self, db):
        """outcome filter narrows results and keeps `total` consistent with the page."""
        await _insert_rule(db)
        await insert_test_card(db, card_id="card_test")
        await _insert_firing(db, 1, 1, "card_test", [{"success": True}])
        await _insert_firing(db, 2, 1, "card_test", [{"success": False, "error": "e"}], error="e")
        await _insert_firing(db, 3, 1, "card_test",
                             [{"success": True, "skipped": True, "reason": "r"}])

        for outcome, expected_id in [("success", 1), ("error", 2), ("skipped", 3)]:
            data = (await _get(f"/processing-rules/firings?outcome={outcome}")).json()
            assert data["total"] == 1, outcome
            assert len(data["entries"]) == 1
            assert data["entries"][0]["id"] == expected_id
            assert data["entries"][0]["outcome"] == outcome

    async def test_filter_by_rule_id(self, db):
        await _insert_rule(db, rule_id=1, name="Rule One")
        await _insert_rule(db, rule_id=2, name="Rule Two")
        await insert_test_card(db, card_id="card_test")
        await _insert_firing(db, 1, 1, "card_test", [{"success": True}])
        await _insert_firing(db, 2, 2, "card_test", [{"success": True}])

        data = (await _get("/processing-rules/firings?rule_id=2")).json()
        assert data["total"] == 1
        assert data["entries"][0]["rule_id"] == 2
        assert data["entries"][0]["rule_name"] == "Rule Two"

    async def test_search(self, db):
        await _insert_rule(db)
        await insert_test_card(db, card_id="card_test", header="Special Card Header")
        await _insert_firing(db, 1, 1, "card_test", [{"success": True}])

        hit = (await _get("/processing-rules/firings?search=Special")).json()
        assert hit["total"] == 1
        miss = (await _get("/processing-rules/firings?search=nonexistent")).json()
        assert miss["total"] == 0

    async def test_pagination(self, db):
        await _insert_rule(db)
        await insert_test_card(db, card_id="card_test")
        for i in range(1, 4):
            await _insert_firing(db, i, 1, "card_test", [{"success": True}],
                                 fired_at=f"2026-06-1{i}T00:00:00Z")

        page1 = (await _get("/processing-rules/firings?limit=2&offset=0")).json()
        assert page1["total"] == 3
        assert len(page1["entries"]) == 2
        # ORDER BY fired_at DESC → newest first
        assert page1["entries"][0]["id"] == 3
        page2 = (await _get("/processing-rules/firings?limit=2&offset=2")).json()
        assert len(page2["entries"]) == 1
        assert page2["entries"][0]["id"] == 1

    async def test_enrichment(self, db):
        """rule_name, card_header, and platform are hydrated via LEFT JOINs."""
        await _insert_rule(db, name="My Rule")
        await insert_test_card(db, card_id="card_test", header="Card XYZ")  # event platform=jira
        await _insert_firing(db, 1, 1, "card_test", [{"success": True}], event_id="evt_test")

        entry = (await _get("/processing-rules/firings")).json()["entries"][0]
        assert entry["rule_name"] == "My Rule"
        assert entry["card_header"] == "Card XYZ"
        assert entry["platform"] == "jira"
        assert entry["action_types"] == ["set_status"]

    async def test_null_platform_for_missing_event(self, db):
        """A firing whose event_id points at no event row degrades to null platform."""
        await _insert_rule(db)
        await insert_test_card(db, card_id="card_test")
        await _insert_firing(db, 1, 1, "card_test", [{"success": True}], event_id="evt_gone")

        entry = (await _get("/processing-rules/firings")).json()["entries"][0]
        assert entry["platform"] is None
