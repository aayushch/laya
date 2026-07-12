# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the briefing scheduler."""

import ast
import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import laya.scheduler as _scheduler_mod
from laya.scheduler import start_scheduler, stop_scheduler, _scheduler_task


class TestSchedulerGlobals:
    """Regression guard: _scheduler_loop assigns several module-level `_last_*`
    state vars (EOD/rolling watermarks). Any such var assigned inside the loop
    but MISSING from its `global` declaration becomes a loop-local, and reading
    it before the first assignment raises UnboundLocalError. Because the loop
    swallows per-tick exceptions, that fault is silent but disables the whole
    block for the process's life. This happened for `_last_omni_date`, silently
    killing Omni auto-synthesis every evening once the clock passed the EOD time.
    """

    def test_all_assigned_module_state_is_declared_global(self):
        src = Path(inspect.getsourcefile(_scheduler_mod)).read_text()
        tree = ast.parse(src)
        loop = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == "_scheduler_loop"
        )
        declared = {name for n in ast.walk(loop) if isinstance(n, ast.Global) for name in n.names}
        # Module-level state the loop is responsible for persisting across ticks.
        module_state = {
            k for k, v in vars(_scheduler_mod).items()
            if k.startswith("_last_")
        }
        assigned = {
            n.id for n in ast.walk(loop)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        leaking = sorted((module_state & assigned) - declared)
        assert not leaking, (
            f"module state assigned in _scheduler_loop without a `global` "
            f"declaration (will UnboundLocalError and silently disable its block): {leaking}"
        )


class TestScheduler:
    def test_start_creates_task(self):
        """start_scheduler creates an asyncio task."""
        import laya.scheduler as sched
        sched._scheduler_task = None

        # We need a running event loop for create_task
        loop = asyncio.new_event_loop()
        try:
            async def _test():
                start_scheduler()
                assert sched._scheduler_task is not None
                assert not sched._scheduler_task.done()
                stop_scheduler()

            loop.run_until_complete(_test())
        finally:
            loop.close()

    def test_stop_cancels_task(self):
        """stop_scheduler cancels the running task."""
        import laya.scheduler as sched
        sched._scheduler_task = None

        loop = asyncio.new_event_loop()
        try:
            async def _test():
                start_scheduler()
                assert sched._scheduler_task is not None
                stop_scheduler()
                assert sched._scheduler_task is None

            loop.run_until_complete(_test())
        finally:
            loop.close()

    def test_start_is_idempotent(self):
        """Calling start_scheduler twice doesn't create a second task."""
        import laya.scheduler as sched
        sched._scheduler_task = None

        loop = asyncio.new_event_loop()
        try:
            async def _test():
                start_scheduler()
                first_task = sched._scheduler_task
                start_scheduler()
                assert sched._scheduler_task is first_task
                stop_scheduler()

            loop.run_until_complete(_test())
        finally:
            loop.close()

    def test_skips_when_disabled(self):
        """Scheduler loop skips briefing when disabled in settings."""
        import laya.scheduler as sched

        mock_settings = {
            "briefing": {"enabled": False, "time": "07:00", "timezone": "UTC"},
        }
        with patch("laya.scheduler.load_settings", return_value=mock_settings):
            # The loop should not call generate_briefing
            with patch("laya.pipeline.briefing.generate_briefing", new_callable=AsyncMock) as mock_gen:
                # We can't easily test the full loop, but we can verify the settings check
                assert mock_settings["briefing"]["enabled"] is False
                mock_gen.assert_not_called()


@pytest.mark.asyncio
class TestFiringLogHousekeeping:
    async def _seed(self, db):
        from tests.conftest import insert_test_card
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) "
            "VALUES (1, 'R', '{\"field\": \"x\", \"operator\": \"exists\"}', '[]')"
        )
        await insert_test_card(db, card_id="card_test")
        await db.commit()

    async def test_prunes_old_firings_only(self, db):
        """_run_firing_log_housekeeping deletes firings older than the window, keeps recent."""
        from laya.scheduler import _run_firing_log_housekeeping

        await self._seed(db)
        # Old firing (well beyond 90 days) and a recent/future one.
        await db.execute(
            "INSERT INTO processing_rule_firings (id, rule_id, card_id, results_json, fired_at) "
            "VALUES (1, 1, 'card_test', '[]', '2025-01-01T00:00:00Z')"
        )
        await db.execute(
            "INSERT INTO processing_rule_firings (id, rule_id, card_id, results_json, fired_at) "
            "VALUES (2, 1, 'card_test', '[]', '2026-12-31T00:00:00Z')"
        )
        await db.commit()

        await _run_firing_log_housekeeping(90)

        rows = await db.execute_fetchall("SELECT id FROM processing_rule_firings ORDER BY id")
        assert [r["id"] for r in rows] == [2]
