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

        captured = {"cards": 0}

        async def fake_llm_call(**kwargs):
            msgs = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in msgs if m["role"] == "user"), "")
            captured["cards"] = user_msg.count("card_id: card_cap_")

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

        # With threshold=50 → cap=150, all 130 cards should flow through.
        assert captured["cards"] == 130

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

        captured = {"cards": 0}

        async def fake_llm_call(**kwargs):
            msgs = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in msgs if m["role"] == "user"), "")
            captured["cards"] = user_msg.count("card_id: card_floor_")

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

        # With threshold=0 → cap=100, only 100 of the 130 cards should be included.
        assert captured["cards"] == 100


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
