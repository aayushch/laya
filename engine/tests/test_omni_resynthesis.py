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
                # LLM carries the resolved item forward anyway.
                parsed = {"sections": [
                    {"type": "attention", "label": None, "items": [
                        {"text": "PROJ-1 awaiting your review",
                         "source_cards": ["card_resolved"],
                         "entity_ids": ["jira:ticket:PROJ-1"], "platforms": ["jira"],
                         "priority": "HIGH", "pinned": False}
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
