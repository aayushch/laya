# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the pipeline concurrency semaphore.

The bug: _get_semaphore() rebuilt the semaphore whenever its live permit count
differed from the configured limit — i.e. any time events held permits — handing
out a fresh full set of permits every poll and defeating the concurrency cap
(user sets 2, sees a flood hit the local model)."""

from unittest.mock import patch

import pytest

from laya.pipeline import queue as q


def _reset():
    q._semaphore = None
    q._semaphore_limit = None


@pytest.mark.asyncio
async def test_semaphore_reused_while_permits_are_held():
    """With the limit unchanged, a poll that runs while events hold permits must
    reuse the SAME semaphore — not mint a new one with the permits freed."""
    _reset()
    with patch.object(q, "_get_pipeline_settings", return_value={"max_concurrent_events": 2}):
        sem1 = q._get_semaphore()
        # Two in-flight events saturate both permits.
        await sem1.acquire()
        await sem1.acquire()
        assert sem1._value == 0  # noqa: SLF001 — fully saturated

        # Next poll cycle. Under the old bug this returned a brand-new Semaphore(2)
        # with two free permits, so a third + fourth event could run concurrently.
        sem2 = q._get_semaphore()
        assert sem2 is sem1
        assert sem2._value == 0  # noqa: SLF001 — backpressure preserved

        sem1.release()
        sem1.release()


@pytest.mark.asyncio
async def test_semaphore_rebuilt_when_limit_changes():
    """A genuine config change (user moves the slider) must resize the pool."""
    _reset()
    with patch.object(q, "_get_pipeline_settings", return_value={"max_concurrent_events": 2}):
        sem1 = q._get_semaphore()
        assert q._get_semaphore() is sem1  # stable across calls at the same limit

    with patch.object(q, "_get_pipeline_settings", return_value={"max_concurrent_events": 4}):
        sem2 = q._get_semaphore()
        assert sem2 is not sem1
        assert sem2._value == 4  # noqa: SLF001 — new size takes effect


@pytest.mark.asyncio
async def test_effective_concurrency_stays_at_limit_across_polls():
    """End-to-end: however many poll cycles run while work is in flight, at most
    `limit` permits are ever available at once."""
    _reset()
    with patch.object(q, "_get_pipeline_settings", return_value={"max_concurrent_events": 2}):
        held = []
        # Simulate three consecutive polls each trying to grab a permit while the
        # earlier ones are still running.
        for _ in range(3):
            sem = q._get_semaphore()
            if sem._value > 0:  # noqa: SLF001 — only acquire if a real slot is free
                await sem.acquire()
                held.append(sem)
        # The cap is 2 — the third poll must have found no free permit.
        assert len(held) == 2
        for s in held:
            s.release()
