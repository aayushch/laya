"""Tests for Omni resynthesis: event_threshold clamp, fetch cap, failure watermark."""

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
class TestResynthesisFailureWatermark:
    """An LLM failure advances omni_last_attempt so the next run skips the failed batch."""

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
            # Backfill created_at to a known time ordering
            await db.execute(
                "UPDATE action_cards SET created_at = ? WHERE card_id = ?",
                (ts, f"card_{prefix}_{i}"),
            )
        await db.commit()

    async def test_watermark_written_on_llm_failure(self, db):
        from laya.pipeline import omni as omni_pipeline

        await self._seed_cards(db, count=3)

        with patch.object(
            omni_pipeline, "llm_call", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            result = await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        assert result is None  # failure returns None

        watermark = await db.execute_fetchall(
            "SELECT last_attempt_at FROM omni_last_attempt WHERE space_id = ?", ("default",)
        )
        assert watermark, "watermark row must be written even on LLM failure"
        assert watermark[0]["last_attempt_at"]

    async def test_next_run_skips_failed_batch(self, db):
        """After a failure, the next attempt only sees cards created AFTER the watermark."""
        from laya.pipeline import omni as omni_pipeline

        # Batch 1: 3 cards, LLM fails — watermark advances past them.
        await self._seed_cards(db, count=3, prefix="batch1")

        with patch.object(
            omni_pipeline, "llm_call", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            await omni_pipeline._resynthesize_space(
                db, "default", density="compact", snapshot_type="manual", event_threshold=50
            )

        # Batch 2: 2 new cards created AFTER batch1. Succeed this time and assert
        # that only the 2 new cards were passed to the LLM.
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
            # Capture the user message to count cards passed through.
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
        # Batch 1 card_ids must NOT appear in the prompt.
        for i in range(3):
            assert f"card_batch1_{i}" not in user_msg
        # Batch 2 card_ids must appear.
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
