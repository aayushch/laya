# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared per-event related-context memo (review §3/§4 — P6-7)."""

from unittest.mock import AsyncMock, patch

import pytest

from laya.pipeline.related_context import (
    clear_related_context_cache,
    query_related_context,
)


@pytest.mark.asyncio
async def test_embedding_search_computed_once_per_event(sample_event):
    """Two stages asking for the same event share one embedding+search; each
    gets its requested breadth sliced from the single top-5 result."""
    clear_related_context_cache()
    items = [{"id": f"m{i}"} for i in range(5)]
    with patch("laya.pipeline.related_context.memory_search",
               new_callable=AsyncMock, return_value=items) as mock_search:
        r_router = await query_related_context(sample_event, n_results=3)
        r_worker = await query_related_context(sample_event, n_results=5)

    mock_search.assert_called_once()          # embedded + searched exactly once
    assert r_router == items[:3]              # router sliced to 3
    assert r_worker == items[:5]              # worker got all 5


@pytest.mark.asyncio
async def test_distinct_events_search_separately(sample_event, slack_event):
    clear_related_context_cache()
    with patch("laya.pipeline.related_context.memory_search",
               new_callable=AsyncMock, return_value=[]) as mock_search:
        await query_related_context(sample_event, 3)
        await query_related_context(slack_event, 3)
    assert mock_search.call_count == 2


@pytest.mark.asyncio
async def test_search_failure_returns_empty_and_is_cached(sample_event):
    clear_related_context_cache()
    with patch("laya.pipeline.related_context.memory_search",
               new_callable=AsyncMock, side_effect=RuntimeError("chroma down")) as mock_search:
        r1 = await query_related_context(sample_event, 3)
        r2 = await query_related_context(sample_event, 3)
    assert r1 == [] and r2 == []
    mock_search.assert_called_once()          # failure result memoized too
