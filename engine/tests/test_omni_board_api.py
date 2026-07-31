# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Omni Situation Board / Item Page endpoints.

Covers the additions behind the board redesign: live item decoration, the
change-summary range endpoint, the event-volume instrument, the one-call item
drill-down, and the schedule fields on the resynthesis status.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from laya.pipeline.omni_change import compute_item_key


def _client():
    from laya.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _insert_event(db, event_id, platform, ts, raw_type="pull_request_opened", space="default"):
    await db.execute(
        """INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type,
                               actor_name, subject_type, subject_id, subject_title,
                               raw_json, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, _ts(ts), platform, raw_type, "Andreu Botella", "pull_request",
         event_id, "A PR", "{}", space),
    )


async def _insert_card(
    db, card_id, event_id, *, status="ready", priority="HIGH", entity_id=None,
    created_at=None, resolved_at=None, space="default", source_ref="PR #1451",
):
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, created_at, priority, persona, category, header, summary,
            status, privacy_tier, has_workspace, entity_id, source_ref, resolved_at, space_id)
           VALUES (?, ?, ?, ?, 'ENGINEER', 'code_review', ?, 'summary text',
                   ?, 2, 0, ?, ?, ?, ?)""",
        (card_id, event_id, created_at or _ts(datetime(2026, 5, 4, 9, 0)), priority,
         f"Header for {card_id}", status, entity_id, source_ref, resolved_at, space),
    )


async def _insert_snapshot(
    db, version, content, *, snapshot_type="scheduled", card_ids=None,
    is_delta=0, base_version=None, change_summary=None, generated_at=None, space="default",
):
    await db.execute(
        """INSERT INTO omni_snapshots
           (snapshot_id, space_id, version, generated_at, snapshot_type, content_json,
            card_ids, events_processed, is_delta, base_version, change_summary_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"omni_v{version}", space, version,
            generated_at or _ts(datetime(2026, 5, 4, 17, 0)),
            snapshot_type, json.dumps(content), json.dumps(card_ids or []),
            len(card_ids or []), is_delta, base_version,
            json.dumps(change_summary) if change_summary is not None else None,
        ),
    )


def _snapshot(sections, stats=None):
    return {
        "sections": sections,
        "stats": stats or {"events_processed": 20, "cards_acted_on": 2, "compression_ratio": 0.82},
    }


@pytest.mark.asyncio
class TestLiveDecoration:
    async def test_items_get_item_key_and_live_block(self, db):
        await _insert_event(db, "evt_1", "github", datetime(2026, 5, 4, 9, 0))
        await _insert_card(db, "card_1", "evt_1", status="ready", priority="HIGH",
                           entity_id="github:pr:1451")
        await _insert_snapshot(db, 1, _snapshot([
            {"type": "recent", "label": None, "items": [
                {"text": "9 PRs reviewed", "source_cards": ["card_1"],
                 "platforms": ["github"], "priority": "MEDIUM",
                 "entity_ids": ["github:pr:1451"]},
            ]},
        ]), card_ids=["card_1"])
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni")).json()

        item = data["sections"][0]["items"][0]
        assert item["item_key"] == compute_item_key("recent", ["github:pr:1451"])
        assert item["live"]["max_priority"] == "HIGH"       # live, not the frozen MEDIUM
        assert item["live"]["all_resolved"] is False
        assert item["live"]["resolved_count"] == 0
        assert item["live"]["missing_count"] == 0
        assert item["live"]["platform_counts"] == {"github": 1}
        assert item["live"]["oldest_created_at"] is not None

    async def test_resolved_cards_report_as_resolved(self, db):
        await _insert_event(db, "evt_1", "github", datetime(2026, 5, 4, 9, 0))
        await _insert_card(db, "card_1", "evt_1", status="done", entity_id="github:pr:1")
        await _insert_snapshot(db, 1, _snapshot([
            {"type": "attention", "label": None, "items": [
                {"text": "PR needs review", "source_cards": ["card_1"],
                 "platforms": ["github"], "priority": "CRITICAL",
                 "entity_ids": ["github:pr:1"]},
            ]},
        ]), card_ids=["card_1"])
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni")).json()

        live = data["sections"][0]["items"][0]["live"]
        assert live["all_resolved"] is True
        assert live["resolved_count"] == 1
        # A resolved subject has no live priority even though the item still says CRITICAL
        assert live["max_priority"] is None

    async def test_missing_cards_are_counted(self, db):
        await _insert_snapshot(db, 1, _snapshot([
            {"type": "recent", "label": None, "items": [
                {"text": "aggregate", "source_cards": ["gone_1", "gone_2"],
                 "platforms": [], "priority": "LOW", "entity_ids": ["x:1"]},
            ]},
        ]))
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni")).json()

        assert data["sections"][0]["items"][0]["live"]["missing_count"] == 2

    async def test_empty_snapshot_still_returns_change_summary_key(self, db):
        async with _client() as client:
            data = (await client.get("/omni")).json()
        assert data["change_summary"] is None


@pytest.mark.asyncio
class TestChangesEndpoint:
    async def _seed_range(self, db):
        await _insert_snapshot(db, 1, _snapshot([]), change_summary=None)
        await _insert_snapshot(db, 2, _snapshot([]), snapshot_type="incremental",
                               change_summary={"added": [{"item_key": "k1", "text": "A"}],
                                               "folded": [], "resolved": [],
                                               "counts": {"added": 1, "folded": 0, "resolved": 0}})
        await _insert_snapshot(db, 3, _snapshot([]), change_summary={
            "added": [], "folded": [],
            "resolved": [{"item_key": "k1", "text": "A", "section": "attention"}],
            "counts": {"added": 0, "folded": 0, "resolved": 1}})
        await db.commit()

    async def test_merges_across_the_range(self, db):
        await self._seed_range(db)
        async with _client() as client:
            data = (await client.get("/omni/changes?base=1&to=3")).json()

        assert data["base_version"] == 1
        assert data["to_version"] == 3
        assert data["versions_compared"] == 2
        # Added at v2 then resolved at v3 → one entry, final state wins
        assert data["counts"] == {"added": 0, "folded": 0, "resolved": 1}

    async def test_base_is_exclusive(self, db):
        await self._seed_range(db)
        async with _client() as client:
            data = (await client.get("/omni/changes?base=2&to=3")).json()
        assert data["versions_compared"] == 1
        assert data["counts"]["resolved"] == 1

    async def test_pre_migration_versions_are_reported_not_hidden(self, db):
        await self._seed_range(db)
        async with _client() as client:
            data = (await client.get("/omni/changes?base=0&to=3")).json()
        assert data["unsummarized_versions"] == [1]

    async def test_defaults_to_previous_version(self, db):
        await self._seed_range(db)
        async with _client() as client:
            data = (await client.get("/omni/changes")).json()
        assert data["to_version"] == 3
        assert data["base_version"] == 2

    async def test_empty_space_does_not_error(self, db):
        async with _client() as client:
            resp = await client.get("/omni/changes")
        assert resp.status_code == 200
        assert resp.json()["counts"] == {"added": 0, "folded": 0, "resolved": 0}


@pytest.mark.asyncio
class TestVolumeEndpoint:
    async def test_series_covers_every_day_including_empty_ones(self, db):
        # Anchored to the UTC day start, not "now - N hours": the endpoint buckets
        # by calendar day, so a relative offset silently lands on yesterday
        # whenever the suite runs shortly after midnight UTC.
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        await _insert_event(db, "evt_a", "github", day_start + timedelta(minutes=1))
        await _insert_event(db, "evt_b", "gmail", day_start + timedelta(minutes=2))
        await _insert_event(db, "evt_c", "gmail", day_start - timedelta(days=3))
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni/volume?days=14")).json()

        assert len(data["series"]) == 14
        assert data["total"] == 3
        assert data["today"] == 2
        assert data["platforms"] == {"gmail": 2, "github": 1}   # sorted by count desc
        assert data["series"][-1]["date"] == data["today_date"]

    async def test_window_excludes_older_events(self, db):
        now = datetime.now(timezone.utc)
        await _insert_event(db, "evt_old", "slack", now - timedelta(days=40))
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni/volume?days=14")).json()

        assert data["total"] == 0

    async def test_days_is_clamped(self, db):
        async with _client() as client:
            data = (await client.get("/omni/volume?days=9999")).json()
        assert data["days"] == 90


@pytest.mark.asyncio
class TestItemEndpoint:
    async def _seed_item(self, db):
        await _insert_event(db, "evt_1", "github", datetime(2026, 5, 4, 9, 12),
                            raw_type="pull_request_opened")
        await _insert_event(db, "evt_2", "github", datetime(2026, 5, 4, 14, 22),
                            raw_type="pull_request_closed")
        await _insert_card(db, "card_open", "evt_1", status="ready",
                           entity_id="github:pr:1451", source_ref="PR #1451")
        await _insert_card(db, "card_merged", "evt_2", status="done",
                           entity_id="github:pr:1447", source_ref="PR #1447",
                           resolved_at=_ts(datetime(2026, 5, 4, 14, 22)))
        item = {
            "text": "9 PRs reviewed — 4 merged, 3 awaiting your review",
            "source_cards": ["card_open", "card_merged", "card_gone"],
            "platforms": ["github"], "priority": "MEDIUM",
            "entity_ids": ["github:pr:1451", "github:pr:1447"],
        }
        await _insert_snapshot(db, 1, _snapshot([
            {"type": "recent", "label": None, "items": [item]},
        ]), card_ids=["card_open", "card_merged"])
        await db.commit()
        return compute_item_key("recent", item["entity_ids"])

    async def test_returns_claim_cards_and_buckets(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            resp = await client.get(f"/omni/item?section=recent&item={key}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["section"] == "recent"
        assert data["version"] == 1
        assert data["item"]["text"].startswith("9 PRs reviewed")
        buckets = {c["card_id"]: c["bucket"] for c in data["cards"]}
        assert buckets == {"card_open": "awaiting_you", "card_merged": "resolved"}

    async def test_unloadable_cards_are_named_not_dropped(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            data = (await client.get(f"/omni/item?section=recent&item={key}")).json()

        assert data["missing_card_ids"] == ["card_gone"]
        assert len(data["cards"]) == 2

    async def test_cards_keep_the_snapshot_order(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            data = (await client.get(f"/omni/item?section=recent&item={key}")).json()
        assert [c["card_id"] for c in data["cards"]] == ["card_open", "card_merged"]

    async def test_share_of_day_is_computed(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            data = (await client.get(f"/omni/item?section=recent&item={key}")).json()
        assert data["share_of_day"]["cards"] == 3

    async def test_lineage_reports_first_appearance(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            data = (await client.get(f"/omni/item?section=recent&item={key}")).json()

        lineage = data["lineage"]
        assert lineage["first_version"] == 1
        assert lineage["versions_carried"] == 1
        assert lineage["section_history"][0]["section"] == "recent"
        assert lineage["next_fold"]["to_section"] == "period"

    async def test_unknown_item_is_404_not_an_empty_page(self, db):
        await self._seed_item(db)
        async with _client() as client:
            resp = await client.get("/omni/item?section=recent&item=deadbeef1234")
        assert resp.status_code == 404

    async def test_missing_item_param_is_400(self, db):
        async with _client() as client:
            resp = await client.get("/omni/item")
        assert resp.status_code in (400, 404)

    async def test_item_is_found_without_the_section_hint(self, db):
        key = await self._seed_item(db)
        async with _client() as client:
            data = (await client.get(f"/omni/item?item={key}")).json()
        assert data["section"] == "recent"


@pytest.mark.asyncio
class TestLineageAcrossVersions:
    async def test_item_followed_through_a_fold(self, db):
        """The item_key changes when a line folds (section is part of the key),
        so lineage must correlate on entity_ids instead."""
        recent_item = {"text": "3 PRs opened", "source_cards": ["c1"], "platforms": ["github"],
                       "priority": "MEDIUM", "entity_ids": ["github:pr:1"]}
        period_item = {"text": "23 PRs merged this week", "source_cards": ["c1"],
                       "platforms": ["github"], "priority": "LOW",
                       "entity_ids": ["github:pr:1"]}
        await _insert_snapshot(db, 1, _snapshot([{"type": "recent", "label": None,
                                                  "items": [recent_item]}]),
                               generated_at=_ts(datetime(2026, 5, 4, 12, 0)))
        await _insert_snapshot(db, 2, _snapshot([{"type": "period", "label": None,
                                                  "items": [period_item]}]),
                               generated_at=_ts(datetime(2026, 5, 4, 17, 0)))
        await db.commit()

        key = compute_item_key("period", ["github:pr:1"])
        async with _client() as client:
            data = (await client.get(f"/omni/item/lineage?section=period&item={key}")).json()

        assert data["versions_carried"] == 2
        assert data["first_version"] == 1
        assert [h["section"] for h in data["section_history"]] == ["recent", "period"]
        # The text was rewritten once as it folded
        assert data["rewrite_count"] == 1

    async def test_lineage_replays_a_delta_chain(self, db):
        base_item = {"text": "base line", "source_cards": ["c1"], "platforms": [],
                     "priority": "LOW", "entity_ids": ["x:1"]}
        await _insert_snapshot(db, 1, _snapshot([{"type": "recent", "label": None,
                                                  "items": [base_item]}]))
        # An incremental delta that appends a second, unrelated item
        await _insert_snapshot(
            db, 2,
            {"added_items": [{"text": "new line", "source_cards": ["c2"], "platforms": [],
                              "priority": "LOW", "entity_ids": ["x:2"]}],
             "fused_updates": {}},
            snapshot_type="incremental", is_delta=1, base_version=1,
        )
        await db.commit()

        key = compute_item_key("recent", ["x:1"])
        async with _client() as client:
            data = (await client.get(f"/omni/item/lineage?item={key}")).json()

        # Present in both versions — the delta replay must not lose the base item
        assert data["versions_carried"] == 2
        assert data["rewrite_count"] == 0


@pytest.mark.asyncio
class TestResynthesisStatusSchedule:
    async def test_schedule_fields_present(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "laya.api.omni_api.load_settings",
            lambda: {"omni": {"enabled": True, "resynthesis_time": "23:00",
                              "rolling_interval_hours": 6, "event_threshold": 50,
                              "timezone": "UTC"}},
        )
        await _insert_snapshot(db, 1, _snapshot([]), snapshot_type="scheduled",
                               generated_at=_ts(datetime(2026, 5, 4, 17, 0)))
        await db.commit()

        async with _client() as client:
            data = (await client.get("/omni/resynthesis/status")).json()

        assert data["in_progress"] is False
        assert data["interval_hours"] == 6
        assert data["event_threshold"] == 50
        assert data["events_since_last"] == 0
        assert data["next_scheduled_at"] is not None

    async def test_disabled_omni_has_no_next_synthesis(self, db, monkeypatch):
        monkeypatch.setattr(
            "laya.api.omni_api.load_settings", lambda: {"omni": {"enabled": False}}
        )
        async with _client() as client:
            data = (await client.get("/omni/resynthesis/status")).json()
        assert data["next_scheduled_at"] is None
