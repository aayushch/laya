# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for Omni resynthesis: event_threshold clamp, fetch cap, failure watermark,
resolution/de-escalation dropping from the attention section."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card


@pytest.mark.asyncio
class TestEventThresholdClamp:
    """PUT /settings clamps omni.event_threshold to [0, 100]."""

    async def test_above_max_is_clamped(self, db):
        from laya.main import app

        transport = ASGITransport(app=app)
        with patch("laya.api.settings_api.load_settings") as mock_load, \
             patch("laya.api.settings_api.save_settings") as mock_save:
            mock_load.return_value = {"omni": {"event_threshold": 50}}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put("/settings", json={"omni": {"event_threshold": 500}})

        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["omni"]["event_threshold"] == 100

    async def test_zero_preserved(self, db):
        from laya.main import app

        transport = ASGITransport(app=app)
        with patch("laya.api.settings_api.load_settings") as mock_load, \
             patch("laya.api.settings_api.save_settings") as mock_save:
            mock_load.return_value = {"omni": {"event_threshold": 50}}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put("/settings", json={"omni": {"event_threshold": 0}})

        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["omni"]["event_threshold"] == 0

    async def test_negative_clamped_to_zero(self, db):
        from laya.main import app

        transport = ASGITransport(app=app)
        with patch("laya.api.settings_api.load_settings") as mock_load, \
             patch("laya.api.settings_api.save_settings") as mock_save:
            mock_load.return_value = {"omni": {"event_threshold": 50}}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put("/settings", json={"omni": {"event_threshold": -10}})

        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["omni"]["event_threshold"] == 0

    async def test_within_range_unchanged(self, db):
        from laya.main import app

        transport = ASGITransport(app=app)
        with patch("laya.api.settings_api.load_settings") as mock_load, \
             patch("laya.api.settings_api.save_settings") as mock_save:
            mock_load.return_value = {"omni": {"event_threshold": 50}}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put("/settings", json={"omni": {"event_threshold": 75}})

        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["omni"]["event_threshold"] == 75


@pytest.mark.asyncio
class TestResynthesisFailureRetry:
    """An LLM failure does NOT advance the watermark — the next run retries the full batch."""

    async def _seed_cards(self, db, count: int, space_id: str = "default", prefix: str = "fail"):
        """Insert `count` cards with increasing created_at timestamps."""
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(count):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db,
                card_id=f"card_{prefix}_{i}",
                event_id=f"evt_{prefix}_{i}",
                space_id=space_id,
            )
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (ts, f"card_{prefix}_{i}"),
            )
        await db.commit()

    async def test_failure_returns_none(self, db):
        from laya.pipeline import omni as omni_pipeline

        await self._seed_cards(db, count=3)

        with patch.object(
            omni_pipeline, "llm_call", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        assert result is None

    async def test_next_run_retries_failed_batch(self, db):
        """After a failure, the next attempt re-includes ALL cards since last success."""
        from laya.pipeline import omni as omni_pipeline

        # Batch 1: 3 cards, LLM fails.
        await self._seed_cards(db, count=3, prefix="batch1")

        with patch.object(
            omni_pipeline, "llm_call", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        # Batch 2: 2 new cards. Succeed this time and assert that ALL 5 cards
        # (batch1 + batch2) are passed to the LLM.
        later = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(2):
            await insert_test_card(
                db, card_id=f"card_batch2_{i}", event_id=f"evt_batch2_{i}", space_id="default"
            )
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (later, f"card_batch2_{i}"),
            )
        await db.commit()

        captured = {}

        async def fake_llm_call(**kwargs):
            msgs = kwargs.get("messages", [])
            captured["user"] = next((m["content"] for m in msgs if m["role"] == "user"), "")

            class R:
                parsed = {"sections": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm_call):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        user_msg = captured.get("user", "")
        # Both batches must appear — failed batch is retried.
        for i in range(3):
            assert f"card_batch1_{i}" in user_msg
        for i in range(2):
            assert f"card_batch2_{i}" in user_msg


@pytest.mark.asyncio
class TestFetchCapMath:
    """fetch_cap = max(100, 3 * event_threshold) when threshold > 0, else 100."""

    async def test_fetch_cap_respects_threshold(self, db):
        """With threshold=50, up to 150 cards should be pulled (not the hardcoded 100)."""
        from laya.pipeline import omni as omni_pipeline

        # Seed 130 cards — only possible for the prompt to include them all if cap is ≥130.
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(130):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db,
                card_id=f"card_cap_{i:03d}",
                event_id=f"evt_cap_{i:03d}",
                space_id="default",
            )
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (ts, f"card_cap_{i:03d}"),
            )
        await db.commit()

        # Cards are now folded across chunked calls (P6-13), so accumulate the
        # per-call counts rather than reading only the last call's message.
        captured = {"cards": 0, "calls": 0}

        async def fake_llm_call(**kwargs):
            msgs = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in msgs if m["role"] == "user"), "")
            captured["cards"] += user_msg.count("card_id: card_cap_")
            captured["calls"] += 1

            class R:
                parsed = {"sections": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm_call):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        # With threshold=50 → cap=150, all 130 cards should flow through, folded
        # across ceil(130 / 40) = 4 chunked calls.
        assert captured["cards"] == 130
        assert captured["calls"] == 4

    async def test_fetch_cap_floor_with_threshold_disabled(self, db):
        """With threshold=0, cap should be the 100 floor."""
        from laya.pipeline import omni as omni_pipeline

        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(130):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db,
                card_id=f"card_floor_{i:03d}",
                event_id=f"evt_floor_{i:03d}",
                space_id="default",
            )
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (ts, f"card_floor_{i:03d}"),
            )
        await db.commit()

        captured = {"cards": 0, "calls": 0}

        async def fake_llm_call(**kwargs):
            msgs = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in msgs if m["role"] == "user"), "")
            captured["cards"] += user_msg.count("card_id: card_floor_")
            captured["calls"] += 1

            class R:
                parsed = {"sections": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm_call):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=0
            )

        # With threshold=0 → cap=100, only 100 of the 130 cards should be
        # included, folded across ceil(100 / 40) = 3 chunked calls.
        assert captured["cards"] == 100
        assert captured["calls"] == 3


@pytest.mark.asyncio
class TestResolutionDrop:
    """Resolved / de-escalated subjects must leave the attention section."""

    async def _insert_snapshot(self, db, space_id, version, generated_at, content,
                               card_ids, snapshot_type="manual"):
        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed, created_at,
                is_delta, base_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"omni_seed_{version}", space_id, version, generated_at, snapshot_type,
             json.dumps(content), json.dumps(card_ids), len(card_ids),
             generated_at, 0, None),
        )
        await db.commit()

    async def _clear_cache(self, space_id="default"):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop(space_id, None)

    async def _load_latest_content(self, db, space_id="default"):
        rows = await db.execute_fetchall(
            """SELECT content_json FROM omni_snapshots
               WHERE space_id = ? ORDER BY version DESC LIMIT 1""",
            (space_id,),
        )
        return json.loads(rows[0]["content_json"])

    def _attention_items(self, content):
        for s in content.get("sections", []):
            if s.get("type") == "attention":
                return s.get("items", [])
        return []

    async def test_resolved_attention_item_is_pruned(self, db):
        """A snapshot attention item whose source card is now done is dropped,
        even if the LLM stubbornly echoes it back (deterministic safety net)."""
        from laya.pipeline import omni as omni_pipeline
        await self._clear_cache()

        now = datetime.now(timezone.utc)
        since_dt = now - timedelta(hours=1)

        # Old card that was in attention, now resolved.
        await insert_test_card(db, card_id="card_resolved", event_id="evt_resolved",
                               status="done", entity_id="jira:ticket:PROJ-1",
                               space_id="default")
        await db.execute(
            "UPDATE action_cards SET created_at = ?, resolved_at = ? WHERE card_id = ?",
            ((now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
             (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), "card_resolved"),
        )
        # A new card so resynthesis isn't skipped.
        await insert_test_card(db, card_id="card_new", event_id="evt_new",
                               status="pending", entity_id="jira:ticket:PROJ-2",
                               space_id="default")
        await db.execute(
            "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
            ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "card_new"),
        )
        await db.commit()

        # Seed a snapshot whose attention section holds the (now-resolved) subject.
        seed_content = {"sections": [
            {"type": "attention", "label": None, "items": [
                {"text": "PROJ-1 awaiting your review", "source_cards": ["card_resolved"],
                 "entity_ids": ["jira:ticket:PROJ-1"], "platforms": ["jira"],
                 "priority": "HIGH", "pinned": False}
            ]},
            {"type": "recent", "label": None, "items": []},
            {"type": "period", "label": None, "items": []},
            {"type": "milestone", "label": None, "items": []},
        ]}
        await self._insert_snapshot(db, "default", 1,
                                    since_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                    seed_content, ["card_resolved"])

        captured = {}

        async def fake_llm_call(**kwargs):
            captured["user"] = next(
                (m["content"] for m in kwargs.get("messages", []) if m["role"] == "user"), "")

            class R:
                # LLM carries the resolved item forward anyway. The new card is
                # folded into recent so the coverage guard lets the run store.
                parsed = {"sections": [
                    {"type": "attention", "label": None, "items": [
                        {"text": "PROJ-1 awaiting your review",
                         "source_cards": ["card_resolved"],
                         "entity_ids": ["jira:ticket:PROJ-1"], "platforms": ["jira"],
                         "priority": "HIGH", "pinned": False}
                    ]},
                    {"type": "recent", "label": None, "items": [
                        {"text": "1 ticket opened (PROJ-2)", "source_cards": ["card_new"],
                         "entity_ids": ["jira:ticket:PROJ-2"], "platforms": ["jira"],
                         "priority": "MEDIUM", "pinned": False}
                    ]},
                    {"type": "period", "label": None, "items": []},
                    {"type": "milestone", "label": None, "items": []},
                ]}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm_call):
            sid = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert sid is not None
        content = await self._load_latest_content(db)
        # The resolved subject must be gone from attention.
        att = self._attention_items(content)
        assert all("card_resolved" not in i.get("source_cards", []) for i in att)

        # Prompt must have surfaced the resolution signals.
        user_msg = captured["user"]
        assert "[RESOLVED SINCE LAST SYNTHESIS]" in user_msg
        assert "jira:ticket:PROJ-1" in user_msg
        assert "[CURRENT STATE OF PRIOR SNAPSHOT ITEMS]" in user_msg
        assert "RESOLVED" in user_msg
        # New card entity tag is shown so the LLM can populate entity_ids.
        assert "[entity: jira:ticket:PROJ-2]" in user_msg

    async def test_entity_ids_backfilled_when_llm_omits(self, db):
        """Items the LLM returns without entity_ids get them backfilled from source cards."""
        from laya.pipeline import omni as omni_pipeline
        await self._clear_cache()

        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_live", event_id="evt_live",
                               status="pending", entity_id="github:pull_request:org/repo/#7",
                               space_id="default")
        await db.execute(
            "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
            ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "card_live"),
        )
        await db.commit()

        async def fake_llm_call(**kwargs):
            class R:
                parsed = {"sections": [
                    {"type": "attention", "label": None, "items": [
                        {"text": "PR #7 awaiting your review",
                         "source_cards": ["card_live"], "entity_ids": [],
                         "platforms": ["github"], "priority": "HIGH", "pinned": False}
                    ]},
                    {"type": "recent", "label": None, "items": []},
                    {"type": "period", "label": None, "items": []},
                    {"type": "milestone", "label": None, "items": []},
                ]}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm_call):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        content = await self._load_latest_content(db)
        att = self._attention_items(content)
        assert len(att) == 1  # live card, not pruned
        assert att[0]["entity_ids"] == ["github:pull_request:org/repo/#7"]


@pytest.mark.asyncio
class TestResynthesisChunking:
    """P6-13: large card bursts are folded across sequential smaller LLM calls
    (chunks of _RESYNTH_CHUNK_SIZE) instead of one truncation-prone mega-call."""

    async def _seed(self, db, count, prefix, space_id="default"):
        """Insert `count` pending cards with strictly increasing created_at."""
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(count):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db, card_id=f"card_{prefix}_{i:03d}",
                event_id=f"evt_{prefix}_{i:03d}", space_id=space_id,
            )
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (ts, f"card_{prefix}_{i:03d}"),
            )
        await db.commit()

    async def _insert_snapshot(self, db, space_id, version, generated_at, content, card_ids):
        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed, created_at,
                is_delta, base_version)
               VALUES (?, ?, ?, ?, 'manual', ?, ?, ?, ?, 0, NULL)""",
            (f"omni_seed_{version}", space_id, version, generated_at,
             json.dumps(content), json.dumps(card_ids), len(card_ids), generated_at),
        )
        await db.commit()

    async def test_small_batch_is_single_call(self, db):
        """A batch at or under the chunk size runs as exactly one LLM call."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        await self._seed(db, omni_pipeline._RESYNTH_CHUNK_SIZE, "small")

        calls = []

        async def fake_llm(**kwargs):
            calls.append(kwargs)

            class R:
                parsed = {"sections": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert len(calls) == 1

    async def test_large_burst_folds_in_ordered_chunks(self, db):
        """95 cards → 3 chunks (≤ 40 each), oldest folded first / newest last,
        and each fold's snapshot is the previous fold's output (feed-forward)."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        await self._seed(db, 95, "burst")

        chunk_ids: list[list[str]] = []
        snapshots_in: list = []
        real_build = omni_pipeline.build_omni_resynthesis_messages

        def spy_build(**kwargs):
            chunk_ids.append([c["card_id"] for c in kwargs["new_cards"]])
            snapshots_in.append(kwargs["current_snapshot"])
            return real_build(**kwargs)

        call_n = {"i": 0}

        async def fake_llm(**kwargs):
            call_n["i"] += 1

            class R:
                # Distinct marker per call so feed-forward is observable.
                parsed = {"sections": [{"type": "recent", "items": [], "marker": call_n["i"]}]}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "build_omni_resynthesis_messages", new=spy_build), \
             patch.object(omni_pipeline, "llm_call", new=fake_llm):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        # ceil(95 / 40) = 3 chunked calls, none over the chunk size.
        assert len(chunk_ids) == 3
        assert all(len(ids) <= omni_pipeline._RESYNTH_CHUNK_SIZE for ids in chunk_ids)
        assert sum(len(ids) for ids in chunk_ids) == 95  # every card folded once

        # Oldest card lands in the FIRST fold, newest in the LAST fold.
        assert "card_burst_000" in chunk_ids[0]
        assert "card_burst_094" in chunk_ids[-1]

        # Feed-forward: first fold has no prior snapshot; each later fold's input
        # snapshot is the immediately preceding fold's output.
        assert snapshots_in[0] is None
        assert snapshots_in[1]["sections"][0]["marker"] == 1
        assert snapshots_in[2]["sections"][0]["marker"] == 2

    async def test_state_inputs_apply_to_first_fold_only(self, db):
        """Snapshot-relative prune hints (item_states) accompany only the first
        chunk; later folds get an empty list."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)

        now = datetime.now(timezone.utc)
        since_dt = now - timedelta(hours=1)

        # A prior snapshot with one attention item → item_states has one entry.
        await insert_test_card(db, card_id="card_prior", event_id="evt_prior",
                               status="pending", entity_id="jira:ticket:PRIOR-1",
                               space_id="default")
        seed_content = {"sections": [
            {"type": "attention", "label": None, "items": [
                {"text": "PRIOR-1 needs review", "source_cards": ["card_prior"],
                 "entity_ids": ["jira:ticket:PRIOR-1"], "platforms": ["jira"],
                 "priority": "HIGH", "pinned": False}
            ]},
            {"type": "recent", "label": None, "items": []},
            {"type": "period", "label": None, "items": []},
            {"type": "milestone", "label": None, "items": []},
        ]}
        await self._insert_snapshot(db, "default", 1,
                                    since_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                    seed_content, ["card_prior"])

        # 50 fresh cards created after the snapshot → 2 chunks.
        base = now - timedelta(minutes=30)
        for i in range(50):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(db, card_id=f"card_state_{i:03d}",
                                   event_id=f"evt_state_{i:03d}", space_id="default")
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (ts, f"card_state_{i:03d}"))
        await db.commit()

        item_states_per_call: list[int] = []
        real_build = omni_pipeline.build_omni_resynthesis_messages

        def spy_build(**kwargs):
            item_states_per_call.append(len(kwargs.get("item_states") or []))
            return real_build(**kwargs)

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "build_omni_resynthesis_messages", new=spy_build), \
             patch.object(omni_pipeline, "llm_call", new=fake_llm):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert len(item_states_per_call) == 2      # 50 cards / 40 = 2 folds
        assert item_states_per_call[0] == 1        # prune hint on the first fold
        assert item_states_per_call[1] == 0        # and only the first

    async def test_later_chunk_failure_discards_run(self, db):
        """If a non-first chunk fails to parse, the whole run is discarded: no
        snapshot is stored and the watermark stays put so all cards retry."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        await self._seed(db, 50, "partial")  # 2 chunks

        call_n = {"i": 0}

        async def fake_llm(**kwargs):
            call_n["i"] += 1

            class R:
                # First fold parses; second fold truncates (parsed=None).
                parsed = {"sections": []} if call_n["i"] == 1 else None
                truncated = call_n["i"] != 1
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert result is None
        assert call_n["i"] == 2  # it did attempt the second fold before failing
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM omni_snapshots WHERE space_id = 'default'", ()
        )
        assert rows[0]["n"] == 0  # nothing persisted — full retry next run


def _degenerate_sections(n_attention=3, n_recent=2):
    """A skeleton/placeholder result like an overloaded local model produced."""
    def dots(k):
        return [{"text": "...", "priority": "...", "source_cards": [], "platforms": []} for _ in range(k)]
    return [
        {"type": "attention", "label": None, "items": dots(n_attention)},
        {"type": "recent", "label": None, "items": dots(n_recent)},
        {"type": "period", "label": None, "items": []},
        {"type": "milestone", "label": None, "items": []},
    ]


class TestDegenerateDetection:
    """Unit coverage for the placeholder-response detector."""

    def test_all_dots_is_degenerate(self):
        from laya.pipeline.omni import _is_degenerate_sections
        assert _is_degenerate_sections(_degenerate_sections()) is True

    def test_healthy_sections_are_not_degenerate(self):
        from laya.pipeline.omni import _is_degenerate_sections
        healthy = [
            {"type": "attention", "items": [
                {"text": "PR #7 needs review", "priority": "HIGH"},
                {"text": "Deploy blocked on CI", "priority": "CRITICAL"},
            ]},
            {"type": "recent", "items": [{"text": "Merged the auth refactor", "priority": "LOW"}]},
        ]
        assert _is_degenerate_sections(healthy) is False

    def test_empty_is_not_degenerate(self):
        from laya.pipeline.omni import _is_degenerate_sections
        assert _is_degenerate_sections([{"type": "attention", "items": []}]) is False

    def test_one_odd_item_does_not_trip_it(self):
        from laya.pipeline.omni import _is_degenerate_sections
        mostly_good = [{"type": "recent", "items": [
            {"text": "Real update one", "priority": "HIGH"},
            {"text": "Real update two", "priority": "LOW"},
            {"text": "...", "priority": "..."},
        ]}]
        assert _is_degenerate_sections(mostly_good) is False  # 1/3 < 0.5


@pytest.mark.asyncio
class TestDegenerateSnapshotGuard:
    """A degenerate LLM result must never be stored (it would poison the
    forward-carried snapshot), and an already-poisoned snapshot must not be fed
    back to the model — it regenerates fresh instead (recovery)."""

    async def _seed(self, db, count, prefix, space_id="default"):
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(count):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db, card_id=f"card_{prefix}_{i:03d}",
                event_id=f"evt_{prefix}_{i:03d}", space_id=space_id,
            )
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (ts, f"card_{prefix}_{i:03d}"))
        await db.commit()

    async def _insert_snapshot(self, db, version, generated_at, content, card_ids, space_id="default"):
        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed, created_at, is_delta, base_version)
               VALUES (?, ?, ?, ?, 'rolling', ?, ?, ?, ?, 0, NULL)""",
            (f"omni_seed_{version}", space_id, version, generated_at,
             json.dumps(content), json.dumps(card_ids), len(card_ids), generated_at),
        )
        await db.commit()

    async def test_degenerate_result_is_not_stored(self, db):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        await self._seed(db, 3, "deg")

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": _degenerate_sections()}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert result is None
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM omni_snapshots WHERE space_id = 'default'", ())
        assert rows[0]["n"] == 0  # the '...' skeleton never hit the DB

    async def test_poisoned_snapshot_is_dropped_and_regenerated(self, db):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)

        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        # A poisoned base snapshot already in the DB.
        await self._insert_snapshot(db, 1, since, {"sections": _degenerate_sections()}, ["old"])
        # A fresh card so resynthesis has something to fold.
        await insert_test_card(db, card_id="card_fresh", event_id="evt_fresh", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "card_fresh"))
        await db.commit()

        seen_snapshots = []
        real_build = omni_pipeline.build_omni_resynthesis_messages

        def spy_build(**kwargs):
            seen_snapshots.append(kwargs["current_snapshot"])
            return real_build(**kwargs)

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": [
                    {"type": "attention", "items": [
                        {"text": "Fresh real item", "priority": "HIGH",
                         "source_cards": ["card_fresh"], "platforms": ["jira"]}]},
                    {"type": "recent", "items": []},
                    {"type": "period", "items": []},
                    {"type": "milestone", "items": []},
                ]}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "build_omni_resynthesis_messages", new=spy_build), \
             patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        # The poisoned snapshot was NOT fed to the model...
        assert seen_snapshots and seen_snapshots[0] is None
        # ...and a healthy snapshot got stored (recovery).
        assert result is not None
        rows = await db.execute_fetchall(
            """SELECT content_json FROM omni_snapshots WHERE space_id = 'default'
               AND is_delta = 0 ORDER BY version DESC LIMIT 1""", ())
        content = json.loads(rows[0]["content_json"])
        texts = [it["text"] for s in content["sections"] for it in s.get("items", [])]
        assert "Fresh real item" in texts
        assert "..." not in texts


@pytest.mark.asyncio
class TestEmptyResultGuard:
    """A resynthesis that folds real cards into ZERO items is a model failure,
    not a legitimately empty state. It must be rejected so it can't wipe the
    accumulated recent items and poison the forward-carried snapshot — the same
    failure mode as the '...' skeleton, but the empty-collapse variant that
    _is_degenerate_sections deliberately exempts (it targets placeholder text)."""

    async def _seed(self, db, count, prefix, space_id="default"):
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(count):
            ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
            await insert_test_card(
                db, card_id=f"card_{prefix}_{i:03d}",
                event_id=f"evt_{prefix}_{i:03d}", space_id=space_id,
            )
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (ts, f"card_{prefix}_{i:03d}"))
        await db.commit()

    async def _insert_snapshot(self, db, version, generated_at, content, card_ids, space_id="default"):
        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed, created_at, is_delta, base_version)
               VALUES (?, ?, ?, ?, 'rolling', ?, ?, ?, ?, 0, NULL)""",
            (f"omni_seed_{version}", space_id, version, generated_at,
             json.dumps(content), json.dumps(card_ids), len(card_ids), generated_at),
        )
        await db.commit()

    def _empty_sections(self):
        """Well-formed 4 sections but every items array empty — what an
        over-compressing local model returned on the high-volume space."""
        return [
            {"type": "attention", "label": None, "items": []},
            {"type": "recent", "label": None, "items": []},
            {"type": "period", "label": None, "items": []},
            {"type": "milestone", "label": None, "items": []},
        ]

    async def test_empty_result_is_not_stored(self, db):
        """LLM folds real cards into zero items → nothing persisted, retry next run."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        await self._seed(db, 3, "empty")

        empty = self._empty_sections()

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": empty}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert result is None
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM omni_snapshots WHERE space_id = 'default'", ())
        assert rows[0]["n"] == 0  # the empty collapse never hit the DB

    async def test_empty_result_preserves_prior_snapshot(self, db):
        """A good snapshot survives an empty resynthesis instead of being
        overwritten with nothing (which would then feed itself forward)."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)

        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        good = {"sections": [
            {"type": "attention", "label": None, "items": [
                {"text": "PR #9 awaiting your review", "source_cards": ["card_old"],
                 "entity_ids": ["github:pull_request:org/repo/#9"], "platforms": ["github"],
                 "priority": "HIGH", "pinned": False}]},
            {"type": "recent", "label": None, "items": []},
            {"type": "period", "label": None, "items": []},
            {"type": "milestone", "label": None, "items": []},
        ]}
        await self._insert_snapshot(db, 1, since, good, ["card_old"])

        # A fresh card so resynthesis runs (isn't skipped for "no new cards").
        await insert_test_card(db, card_id="card_fresh", event_id="evt_fresh", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "card_fresh"))
        await db.commit()

        empty = self._empty_sections()

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": empty}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        assert result is None  # empty result rejected
        # The good snapshot is still the latest — no new (empty) version written.
        rows = await db.execute_fetchall(
            """SELECT version, content_json FROM omni_snapshots
               WHERE space_id = 'default' ORDER BY version DESC LIMIT 1""", ())
        assert rows[0]["version"] == 1
        content = json.loads(rows[0]["content_json"])
        texts = [it["text"] for s in content["sections"] for it in s.get("items", [])]
        assert "PR #9 awaiting your review" in texts

    async def test_all_resolved_prune_to_empty_still_stores(self, db):
        """The guard runs BEFORE the resolved-attention prune: a result whose
        only item is legitimately dropped as resolved still stores (correctly
        empty) — it is NOT mistaken for a model that returned nothing."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)

        now = datetime.now(timezone.utc)
        # A resolved card the LLM will (stubbornly) echo into attention.
        await insert_test_card(db, card_id="card_done", event_id="evt_done",
                               status="done", entity_id="jira:ticket:PROJ-9",
                               space_id="default")
        await db.execute(
            "UPDATE action_cards SET created_at = ?, resolved_at = ? WHERE card_id = ?",
            ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
             (now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"), "card_done"),
        )
        await db.commit()

        async def fake_llm(**kwargs):
            class R:
                parsed = {"sections": [
                    {"type": "attention", "label": None, "items": [
                        {"text": "PROJ-9 awaiting your review", "source_cards": ["card_done"],
                         "entity_ids": ["jira:ticket:PROJ-9"], "platforms": ["jira"],
                         "priority": "HIGH", "pinned": False}]},
                    {"type": "recent", "label": None, "items": []},
                    {"type": "period", "label": None, "items": []},
                    {"type": "milestone", "label": None, "items": []},
                ]}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake_llm):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50)

        # The LLM returned a non-empty result (1 item), so the empty-guard lets
        # it through; the deterministic prune then drops the resolved item.
        assert result is not None
        rows = await db.execute_fetchall(
            """SELECT content_json FROM omni_snapshots WHERE space_id = 'default'
               AND is_delta = 0 ORDER BY version DESC LIMIT 1""", ())
        content = json.loads(rows[0]["content_json"])
        att = [it for s in content["sections"] if s.get("type") == "attention"
               for it in s.get("items", [])]
        assert att == []  # resolved subject pruned, snapshot stored anyway


# ---------------------------------------------------------------------------
# Resynthesis input shaping, the new-card coverage guard, and attention
# carry-forward. Shared helpers keep each case down to the thing it asserts.
# ---------------------------------------------------------------------------

def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _seed_snapshot(db, *, version, generated_at, content, card_ids,
                         snapshot_type="manual", is_delta=0, base_version=None,
                         space_id="default"):
    await db.execute(
        """INSERT INTO omni_snapshots
           (snapshot_id, space_id, version, generated_at, snapshot_type,
            content_json, card_ids, events_processed, created_at,
            is_delta, base_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"omni_seed_{version}", space_id, version, generated_at, snapshot_type,
         json.dumps(content), json.dumps(card_ids), len(card_ids), generated_at,
         is_delta, base_version),
    )
    await db.commit()


def _four_sections(attention=(), recent=(), period=(), milestone=()):
    return [
        {"type": "attention", "label": None, "items": list(attention)},
        {"type": "recent", "label": None, "items": list(recent)},
        {"type": "period", "label": None, "items": list(period)},
        {"type": "milestone", "label": None, "items": list(milestone)},
    ]


def _agg(text, cards, entities, priority="MEDIUM", platforms=("jira",)):
    return {"text": text, "source_cards": list(cards), "entity_ids": list(entities),
            "platforms": list(platforms), "priority": priority, "pinned": False}


def _fake_llm(sections, captured=None):
    async def fake(**kwargs):
        if captured is not None:
            captured["user"] = next(
                (m["content"] for m in kwargs.get("messages", []) if m["role"] == "user"), "")

        class R:
            parsed = {"sections": sections}
            truncated = False
            output_tokens = 10
            model = "test"
        return R()
    return fake


async def _latest_row(db, space_id="default"):
    rows = await db.execute_fetchall(
        """SELECT version, content_json, card_ids, change_summary_json
           FROM omni_snapshots WHERE space_id = ? ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )
    return rows[0]


def _items(content, stype):
    return next((s["items"] for s in content["sections"] if s["type"] == stype), [])


async def _run(db, **kwargs):
    from laya.pipeline import omni as omni_pipeline
    return await omni_pipeline._resynthesize_space(
        db, "default", density="compact", snapshot_type="manual", event_threshold=50,
        **kwargs)


@pytest.mark.asyncio
class TestResynthesisInput:
    async def test_llm_sees_base_aggregates_apart_and_delta_chain_cards(self, db):
        """The prompt's snapshot carries an EMPTY recent, the last run's recent
        aggregates go in their own block, and a card that exists only in the
        delta chain (event time older than the last synthesis) is still folded."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)

        await insert_test_card(db, card_id="card_old", event_id="evt_old", status="done",
                               entity_id="jira:ticket:OLD-1", space_id="default")
        await insert_test_card(db, card_id="card_delta", event_id="evt_delta",
                               entity_id="jira:ticket:LATE-1", space_id="default")
        await insert_test_card(db, card_id="card_fresh", event_id="evt_fresh",
                               entity_id="jira:ticket:NEW-1", space_id="default")
        for cid, dt in (("card_old", now - timedelta(hours=3)),
                        # Event time BEFORE the last synthesis: invisible to the
                        # since-window, reachable only through the delta chain.
                        ("card_delta", now - timedelta(hours=2)),
                        ("card_fresh", now - timedelta(minutes=5))):
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (_ts(dt), cid))
        await db.commit()

        await _seed_snapshot(
            db, version=1, generated_at=_ts(now - timedelta(hours=1)),
            content={"sections": _four_sections(recent=[
                _agg("5 tickets closed (OLD-1 and friends)", ["card_old"], ["jira:ticket:OLD-1"])])},
            card_ids=["card_old"])
        await _seed_snapshot(
            db, version=2, generated_at=_ts(now - timedelta(minutes=30)),
            snapshot_type="incremental", is_delta=1, base_version=1,
            content={"added_items": [{
                "text": "LATE-1 — Test summary", "source_cards": ["card_delta"],
                "platforms": ["jira"], "priority": "HIGH", "pinned": False,
                "bookmarked": False, "entity_id": "jira:ticket:LATE-1"}],
                "fused_updates": {}},
            card_ids=["card_delta"])

        captured = {}
        out = _four_sections(
            recent=[_agg("2 tickets opened (LATE-1, NEW-1)", ["card_delta", "card_fresh"],
                         ["jira:ticket:LATE-1", "jira:ticket:NEW-1"])],
            period=[_agg("5 tickets closed this week", ["card_old"], ["jira:ticket:OLD-1"])],
        )
        with patch.object(omni_pipeline, "llm_call", new=_fake_llm(out, captured)):
            sid = await _run(db)
        assert sid is not None

        user = captured["user"]
        snapshot_block = user[user.index("[CURRENT OMNI SNAPSHOT]"):user.index("[END CURRENT SNAPSHOT]")]
        assert "5 tickets closed (OLD-1 and friends)" not in snapshot_block
        prior_block = user[user.index("[PRIOR RECENT AGGREGATES"):user.index("[END PRIOR RECENT AGGREGATES]")]
        assert "5 tickets closed (OLD-1 and friends)" in prior_block
        assert '"source_cards": ["card_old"]' in prior_block  # ids travel with the aggregate
        assert "card_id:" not in prior_block
        assert "[MEDIUM]" not in prior_block  # a bracket prefix gets copied into output text
        cards_block = user[user.index("[NEW CARDS SINCE LAST SYNTHESIS]"):user.index("[END NEW CARDS]")]
        assert "card_id: card_delta" in cards_block
        assert "card_id: card_fresh" in cards_block
        assert cards_block.index("card_fresh") < cards_block.index("card_delta")  # newest first
        assert "STRUCTURE RULES FOR THIS RUN" in user

        row = await _latest_row(db)
        assert set(json.loads(row["card_ids"])) == {"card_old", "card_delta", "card_fresh"}

        # A stored run writes no outcome row: the snapshot is its own record.
        audit = await db.execute_fetchall(
            "SELECT 1 FROM audit_log WHERE step = 'omni_resynthesis_outcome'")
        assert audit == []

    async def test_first_ever_incremental_base_offers_no_prior_aggregates(self, db):
        """The incremental path's skeleton base holds raw per-card items; they
        are all still new cards, so no prior-recent block is produced."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_a", event_id="evt_a",
                               entity_id="jira:ticket:A-1", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(minutes=5)), "card_a"))
        await db.commit()
        await _seed_snapshot(
            db, version=1, generated_at=_ts(now - timedelta(minutes=4)),
            snapshot_type="incremental",
            content={"sections": _four_sections(recent=[{
                "text": "A-1 — Test summary", "source_cards": ["card_a"], "platforms": ["jira"],
                "priority": "HIGH", "pinned": False, "bookmarked": False,
                "entity_id": "jira:ticket:A-1"}])},
            card_ids=["card_a"])

        captured = {}
        out = _four_sections(recent=[_agg("1 ticket opened (A-1)", ["card_a"], ["jira:ticket:A-1"])])
        with patch.object(omni_pipeline, "llm_call", new=_fake_llm(out, captured)):
            assert await _run(db) is not None
        assert "[PRIOR RECENT AGGREGATES" not in captured["user"]
        assert "card_id: card_a" in captured["user"]

    async def test_prior_recent_split_applies_to_first_fold_only(self, db):
        """Later folds of a chunked run merge into the recent built by the first
        fold, so they get `prior_recent_items=None`."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_old", event_id="evt_old",
                               entity_id="jira:ticket:OLD-1", space_id="default")
        await _seed_snapshot(
            db, version=1, generated_at=_ts(now - timedelta(hours=1)),
            content={"sections": _four_sections(recent=[
                _agg("old aggregate", ["card_old"], ["jira:ticket:OLD-1"])])},
            card_ids=["card_old"])
        base = now - timedelta(minutes=30)
        for i in range(50):  # 50 / 40 → 2 folds
            await insert_test_card(db, card_id=f"card_split_{i:03d}",
                                   event_id=f"evt_split_{i:03d}", space_id="default")
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (_ts(base + timedelta(seconds=i)), f"card_split_{i:03d}"))
        await db.commit()

        seen: list = []
        real_build = omni_pipeline.build_omni_resynthesis_messages

        def spy_build(**kwargs):
            seen.append(kwargs.get("prior_recent_items"))
            return real_build(**kwargs)

        with patch.object(omni_pipeline, "build_omni_resynthesis_messages", new=spy_build), \
             patch.object(omni_pipeline, "llm_call", new=_fake_llm([])):
            await _run(db)

        assert len(seen) == 2
        assert seen[0] is not None and [i["text"] for i in seen[0]] == ["old aggregate"]
        assert seen[1] is None


@pytest.mark.asyncio
class TestCoverageGuard:
    async def test_echo_of_prior_aggregates_is_rejected(self, db):
        """A result that re-emits last run's items and accounts for none of the
        new cards is not stored, so the cards return next run."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_old", event_id="evt_old",
                               entity_id="jira:ticket:OLD-1", space_id="default")
        await insert_test_card(db, card_id="card_new", event_id="evt_new",
                               entity_id="jira:ticket:NEW-1", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(hours=2)), "card_old"))
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(minutes=5)), "card_new"))
        await db.commit()
        prior = _four_sections(recent=[_agg("old aggregate", ["card_old"], ["jira:ticket:OLD-1"])])
        await _seed_snapshot(db, version=1, generated_at=_ts(now - timedelta(hours=1)),
                             content={"sections": prior}, card_ids=["card_old"])

        calls: list[dict] = []
        echo = _fake_llm(prior)

        async def counting(**kwargs):
            calls.append(kwargs)
            return await echo(**kwargs)

        with patch.object(omni_pipeline, "llm_call", new=counting):
            result = await _run(db)

        assert result is None
        assert (await _latest_row(db))["version"] == 1
        assert omni_pipeline._get_gate("default").is_set()

        # The model was asked once to finish the job before the run was rejected.
        assert len(calls) == 2
        repair_msgs = calls[1]["messages"]
        assert calls[1]["step"] == "omni_resynthesis_repair"
        assert repair_msgs[-2]["role"] == "assistant"
        assert json.loads(repair_msgs[-2]["content"])["sections"] == prior
        assert repair_msgs[-1]["role"] == "user"
        assert "cites none of the 1 new cards" in repair_msgs[-1]["content"]

        # The rejection is explained to the user in the audit log, with a
        # digest of what was thrown away.
        rows = await db.execute_fetchall(
            "SELECT success, error, metadata FROM audit_log WHERE step = 'omni_resynthesis_outcome'")
        assert len(rows) == 1
        assert not rows[0]["success"]
        assert "last run's aggregates unchanged" in rows[0]["error"]
        assert "asked once" in rows[0]["error"]
        meta = json.loads(rows[0]["metadata"])
        assert meta["outcome"] == "no_new_coverage"
        assert meta["echo"] is True and meta["repair_attempted"] is True
        assert meta["space_id"] == "default"
        assert meta["output"] == [{"section": "recent", "priority": "MEDIUM",
                                   "text": "old aggregate", "source_cards": 1,
                                   "new_cards_cited": 0}]

    async def test_repair_turn_that_cites_the_cards_is_stored(self, db):
        """An output that summarised the cards but left the ids off is not
        thrown away: the model is shown it and asked to attach them."""
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_old", event_id="evt_old",
                               entity_id="jira:ticket:OLD-1", space_id="default")
        await insert_test_card(db, card_id="card_new", event_id="evt_new",
                               entity_id="jira:ticket:NEW-1", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(hours=2)), "card_old"))
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(minutes=5)), "card_new"))
        await db.commit()
        await _seed_snapshot(db, version=1, generated_at=_ts(now - timedelta(hours=1)),
                             content={"sections": _four_sections(recent=[
                                 _agg("old aggregate", ["card_old"], ["jira:ticket:OLD-1"])])},
                             card_ids=["card_old"])

        uncited = _four_sections(recent=[_agg("1 ticket opened (NEW-1)", ["card_old"], ["jira:ticket:NEW-1"])])
        cited = _four_sections(recent=[_agg("1 ticket opened (NEW-1)", ["card_new"], ["jira:ticket:NEW-1"])])
        answers = [uncited, cited]

        async def fake(**kwargs):
            class R:
                parsed = {"sections": answers.pop(0), "attention_exits": []}
                truncated = False
                output_tokens = 10
                model = "test"
            return R()

        with patch.object(omni_pipeline, "llm_call", new=fake):
            assert await _run(db) is not None

        row = await _latest_row(db)
        assert row["version"] == 2
        assert [i["source_cards"] for i in _items(json.loads(row["content_json"]), "recent")] == [["card_new"]]
        assert await db.execute_fetchall(
            "SELECT 1 FROM audit_log WHERE step = 'omni_resynthesis_outcome'") == []

    async def test_schema_restricts_citations_to_the_cards_given(self, db):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await insert_test_card(db, card_id="card_old", event_id="evt_old",
                               entity_id="jira:ticket:OLD-1", space_id="default")
        await insert_test_card(db, card_id="card_new", event_id="evt_new",
                               entity_id="jira:ticket:NEW-1", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(hours=2)), "card_old"))
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(minutes=5)), "card_new"))
        await db.commit()
        await _seed_snapshot(db, version=1, generated_at=_ts(now - timedelta(hours=1)),
                             content={"sections": _four_sections(recent=[
                                 _agg("old aggregate", ["card_old"], ["jira:ticket:OLD-1"])])},
                             card_ids=["card_old"])
        seen: list[dict] = []
        inner = _fake_llm(_four_sections(recent=[_agg("1 ticket opened", ["card_new"], ["jira:ticket:NEW-1"])]))

        async def spy(**kwargs):
            seen.append(kwargs["response_schema"])
            return await inner(**kwargs)

        with patch.object(omni_pipeline, "llm_call", new=spy):
            assert await _run(db) is not None
        src = seen[0]["schema"]["properties"]["sections"]["items"]["properties"]["items"]["items"]["properties"]["source_cards"]
        assert src["minItems"] == 1
        assert src["items"]["enum"] == ["card_new", "card_old"]


@pytest.mark.asyncio
class TestAttentionPlacementIsTheModels:
    """Omni's first rule: the model alone decides what is in attention. The
    pipeline never promotes by priority, never restores, never moves items —
    the feed already sorts by priority, and a wrong attention list is a prompt
    bug, not something code patches over."""

    async def test_high_and_critical_news_stays_where_the_model_put_it(self, db):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        await _seed_snapshot(db, version=1, generated_at=_ts(now - timedelta(hours=1)),
                             content={"sections": _four_sections()}, card_ids=[])
        for cid, pri, eid in (("card_incident", "CRITICAL", "slack:thread:ops-1"),
                              ("card_pr", "HIGH", "bitbucket:pull_request:756"),
                              ("card_ask", "MEDIUM", "slack:thread:q-1")):
            await insert_test_card(db, card_id=cid, event_id=f"evt_{cid}", priority=pri,
                                   entity_id=eid, space_id="default")
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (_ts(now - timedelta(minutes=5)), cid))
        await db.commit()

        # The model judged: the MEDIUM direct question needs the user; the
        # CRITICAL incident someone else is handling is news; the HIGH PR is history.
        out = _four_sections(
            attention=[_agg("Jin asked you about the warehouse sizing", ["card_ask"],
                            ["slack:thread:q-1"], priority="MEDIUM")],
            recent=[_agg("1 incident (ops handling)", ["card_incident"],
                         ["slack:thread:ops-1"], priority="CRITICAL")],
            period=[_agg("1 PR merged this week (#756)", ["card_pr"],
                         ["bitbucket:pull_request:756"], priority="HIGH")],
        )
        with patch.object(omni_pipeline, "llm_call", new=_fake_llm(out)):
            assert await _run(db) is not None

        content = json.loads((await _latest_row(db))["content_json"])
        assert [i["text"] for i in _items(content, "attention")] == ["Jin asked you about the warehouse sizing"]
        assert [i["text"] for i in _items(content, "recent")] == ["1 incident (ops handling)"]
        assert [i["text"] for i in _items(content, "period")] == ["1 PR merged this week (#756)"]


def _fake_llm_with_exits(sections, exits):
    async def fake(**kwargs):
        class R:
            parsed = {"sections": sections, "attention_exits": exits}
            truncated = False
            output_tokens = 10
            model = "test"
        return R()
    return fake


@pytest.mark.asyncio
class TestAttentionExits:
    """The model's declared exits are recorded as the reason a line left
    attention (E1–E7 in the prompt). Nothing acts on them."""

    async def _seed(self, db, now):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        # Prior attention: a CRITICAL build failure whose card never changes status.
        await insert_test_card(db, card_id="card_fail", event_id="evt_fail", priority="CRITICAL",
                               entity_id="bitbucket_server:pull_request:PR-748", space_id="default")
        # New card on the SAME subject: the build passed.
        await insert_test_card(db, card_id="card_pass", event_id="evt_pass", priority="LOW",
                               entity_id="bitbucket_server:pull_request:PR-748", space_id="default")
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(hours=2)), "card_fail"))
        await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                         (_ts(now - timedelta(minutes=5)), "card_pass"))
        await db.commit()
        await _seed_snapshot(db, version=1, generated_at=_ts(now - timedelta(hours=1)),
                             content={"sections": _four_sections(attention=[
                                 _agg("Build failed on PR-748", ["card_fail"],
                                      ["bitbucket_server:pull_request:PR-748"], priority="CRITICAL")])},
                             card_ids=["card_fail"])

    async def test_declared_exit_is_recorded_with_its_reason(self, db):
        from laya.pipeline import omni as omni_pipeline
        now = datetime.now(timezone.utc)
        await self._seed(db, now)
        out = _four_sections(recent=[
            _agg("PR-748 build green again", ["card_pass", "card_fail"],
                 ["bitbucket_server:pull_request:PR-748"], priority="LOW")])
        exits = [{"entity_ids": ["bitbucket_server:pull_request:PR-748"],
                  "reason": "superseded", "note": "build passed on PR-748"}]
        with patch.object(omni_pipeline, "llm_call", new=_fake_llm_with_exits(out, exits)):
            assert await _run(db) is not None

        row = await _latest_row(db)
        content = json.loads(row["content_json"])
        assert _items(content, "attention") == []           # the model left it out; that stands
        assert [i["text"] for i in _items(content, "recent")] == ["PR-748 build green again"]
        summary = json.loads(row["change_summary_json"])
        # The exit consumes the recent line on the same subject: one resolved
        # entry with the reason, not a fold and not a separate "added".
        assert summary["counts"] == {"added": 0, "folded": 0, "resolved": 1}
        resolved = summary["resolved"][0]
        assert resolved["section"] == "attention"
        assert resolved["reason"] == "superseded"
        assert resolved["note"] == "build passed on PR-748"

    async def test_exits_are_not_fed_forward_between_folds(self, db):
        from laya.pipeline import omni as omni_pipeline
        omni_pipeline._latest_cache.pop("default", None)
        now = datetime.now(timezone.utc)
        base = now - timedelta(minutes=30)
        for i in range(50):  # 2 folds
            await insert_test_card(db, card_id=f"card_x{i:03d}", event_id=f"evt_x{i:03d}",
                                   priority="LOW", space_id="default")
            await db.execute("UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                             (_ts(base + timedelta(seconds=i)), f"card_x{i:03d}"))
        await db.commit()

        snapshots_seen: list = []
        real_build = omni_pipeline.build_omni_resynthesis_messages

        def spy_build(**kwargs):
            snapshots_seen.append(kwargs.get("current_snapshot"))
            return real_build(**kwargs)

        exits = [{"entity_ids": ["jira:ticket:Z-1"], "reason": "expired", "note": "meeting happened"}]
        with patch.object(omni_pipeline, "build_omni_resynthesis_messages", new=spy_build), \
             patch.object(omni_pipeline, "llm_call", new=_fake_llm_with_exits([], exits)):
            await _run(db)
        assert len(snapshots_seen) == 2
        assert "attention_exits" not in (snapshots_seen[1] or {})


class TestCurrentAttentionBlock:
    """The user turn states the attention list explicitly — including that it is
    empty — so the model decides what belongs there rather than reading
    `"items": []` as nothing to do."""

    def _build(self, snapshot, item_states=None):
        from laya.llm.prompts.omni import build_omni_resynthesis_messages
        msgs = build_omni_resynthesis_messages(
            current_snapshot=snapshot, new_cards=[{"card_id": "c1", "header": "h", "summary": "s",
                                                   "priority": "HIGH", "source_platform": "jira"}],
            acted_cards=[], pinned_items=[], density="compact", space_id="default",
            item_states=item_states or [], resolved_cards=[], prior_recent_items=[])
        user = msgs[1]["content"]
        return user[user.index("[CURRENT ATTENTION]"):user.index("[END CURRENT ATTENTION]")]

    def test_empty_attention_is_stated_and_admission_is_asked_for(self):
        block = self._build({"sections": _four_sections()})
        assert "EMPTY" in block
        assert "A1–A6" in block
        assert "decide whether any subject must now be added" in block

    def test_current_items_are_listed_with_live_state(self):
        snapshot = {"sections": _four_sections(attention=[
            _agg("PROJ-1 awaiting your review", ["c9"], ["jira:ticket:PROJ-1"], priority="HIGH")])}
        states = [{"text": "PROJ-1 awaiting your review", "all_resolved": False,
                   "live_max_priority": "HIGH"}]
        block = self._build(snapshot, states)
        assert "1 item(s) currently need the user" in block
        assert "E1–E7" in block
        assert '"text": "PROJ-1 awaiting your review"' in block
        assert '"live": "highest live priority now HIGH"' in block
        assert '"entity_ids": ["jira:ticket:PROJ-1"]' in block

    def test_first_synthesis_without_a_snapshot_still_states_empty(self):
        block = self._build(None)
        assert "EMPTY" in block


class TestOmniSchema:
    def test_without_ids_source_cards_are_unconstrained(self):
        from laya.llm.prompts.omni import get_omni_json_schema
        src = get_omni_json_schema("compact")["schema"]["properties"]["sections"]["items"]["properties"]["items"]["items"]["properties"]["source_cards"]
        assert src["items"] == {"type": "string"}
        assert "minItems" not in src

    def test_with_ids_every_item_must_cite_a_given_card(self):
        from laya.llm.prompts.omni import get_omni_json_schema
        src = get_omni_json_schema("compact", ["b", "a", "", "a"])["schema"]["properties"]["sections"]["items"]["properties"]["items"]["items"]["properties"]["source_cards"]
        assert src["items"] == {"type": "string", "enum": ["a", "b"]}
        assert src["minItems"] == 1
