# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Trace rerun lifecycle.

Regression coverage for the coherence bug where rerun DELETE'd the trace row up
front and then persisted the result under a brand-new trace_id — so an aborted /
cancelled / crashed rerun destroyed the trace and every history link 404'd forever.

The fix (pipeline/trace.py + api/trace_api.py): rerun REUSES the existing trace_id,
_save_trace UPSERTs the row in place, and the row is replaced only on success.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.models.trace import SearchMetadata, TraceRequest, TraceResponse
from laya.pipeline.trace import (
    TraceAlreadyRunning,
    TraceCancelled,
    _cancel_events,
    _save_trace,
    run_trace,
)


async def _seed_trace(
    db,
    trace_id="trace_seed001",
    query="nexus related changes",
    created_at="2026-01-01T00:00:00.000000Z",
    cluster_data=None,
    summary=None,
    narrative=None,
    space_id=None,
):
    """Insert a traces row directly, giving full control over created_at/summary."""
    if cluster_data is None:
        cluster_data = []
    await db.execute(
        """INSERT INTO traces (trace_id, query, created_at, updated_at, narrative,
                               chapters, cluster_data, card_ids, search_metadata,
                               space_id, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trace_id,
            query,
            created_at,
            created_at,
            narrative,
            json.dumps([]),
            json.dumps(cluster_data),
            json.dumps([]),
            SearchMetadata().model_dump_json(),
            space_id,
            summary,
        ),
    )
    await db.commit()


def _fake_inner_factory(created_at="2026-07-16T12:00:00.000000Z"):
    """Build a stub for _run_trace_inner that skips discovery/LLM but exercises the
    real _save_trace upsert (the code path under test)."""

    async def _fake_inner(request, trace_id, _progress, t0):
        resp = TraceResponse(
            trace_id=trace_id,
            query=request.query,
            clusters=[],
            search_metadata=SearchMetadata(),
            created_at=created_at,
            space_id=request.space_id,
        )
        await _save_trace(resp)
        return resp

    return _fake_inner


def _client(raise_app_exceptions=True):
    from laya.main import app

    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestTraceRerun:
    async def test_rerun_preserves_trace_id(self, db):
        """Rerun reuses the original trace_id and the row stays fetchable."""
        await _seed_trace(db, trace_id="trace_abc123")

        with patch("laya.pipeline.trace._run_trace_inner", _fake_inner_factory()):
            async with _client() as client:
                resp = await client.post("/traces/trace_abc123/rerun")

        assert resp.status_code == 200
        assert resp.json()["trace_id"] == "trace_abc123"

        # History link still resolves — the reported symptom was a permanent 404 here.
        async with _client() as client:
            got = await client.get("/traces/trace_abc123")
        assert got.status_code == 200

    async def test_rerun_does_not_delete_on_failure(self, db):
        """A crash mid-run must leave the original trace fully intact."""
        seeded_clusters = [{"cluster_id": "c1", "primary_entity": {
            "entity_id": "jira:ticket:BUG-1", "title": "BUG-1", "platform": "jira"}}]
        await _seed_trace(db, trace_id="trace_fail", cluster_data=seeded_clusters)

        async def _boom(request, trace_id, _progress, t0):
            raise RuntimeError("discovery exploded")

        with patch("laya.pipeline.trace._run_trace_inner", _boom):
            async with _client(raise_app_exceptions=False) as client:
                resp = await client.post("/traces/trace_fail/rerun")

        assert resp.status_code == 500

        rows = await db.execute_fetchall(
            "SELECT cluster_data FROM traces WHERE trace_id = ?", ("trace_fail",)
        )
        assert len(rows) == 1  # row survived
        assert json.loads(rows[0]["cluster_data"]) == seeded_clusters  # unchanged

    async def test_rerun_does_not_delete_on_cancel(self, db):
        """Cancelling a rerun returns 499 and leaves the trace intact."""
        await _seed_trace(db, trace_id="trace_cancel")

        async def _cancel(request, trace_id, _progress, t0):
            raise TraceCancelled(trace_id)

        with patch("laya.pipeline.trace._run_trace_inner", _cancel):
            async with _client() as client:
                resp = await client.post("/traces/trace_cancel/rerun")

        assert resp.status_code == 499
        rows = await db.execute_fetchall(
            "SELECT trace_id FROM traces WHERE trace_id = ?", ("trace_cancel",)
        )
        assert len(rows) == 1

    async def test_rerun_preserves_created_at_updates_updated_at(self, db):
        """created_at stays put (stable history ordering); updated_at advances."""
        await _seed_trace(
            db, trace_id="trace_ts", created_at="2026-01-01T00:00:00.000000Z"
        )

        new_ts = "2026-07-16T12:00:00.000000Z"
        with patch("laya.pipeline.trace._run_trace_inner", _fake_inner_factory(new_ts)):
            async with _client() as client:
                resp = await client.post("/traces/trace_ts/rerun")
        assert resp.status_code == 200

        rows = await db.execute_fetchall(
            "SELECT created_at, updated_at FROM traces WHERE trace_id = ?", ("trace_ts",)
        )
        assert rows[0]["created_at"] == "2026-01-01T00:00:00.000000Z"
        assert rows[0]["updated_at"] == new_ts

    async def test_rerun_clears_stale_summary(self, db):
        """The old top-level summary/narrative (describing the old cluster set) is cleared."""
        await _seed_trace(
            db, trace_id="trace_sum",
            summary="OLD SUMMARY", narrative="OLD NARRATIVE",
        )

        with patch("laya.pipeline.trace._run_trace_inner", _fake_inner_factory()):
            async with _client() as client:
                resp = await client.post("/traces/trace_sum/rerun")
        assert resp.status_code == 200

        rows = await db.execute_fetchall(
            "SELECT summary, narrative FROM traces WHERE trace_id = ?", ("trace_sum",)
        )
        assert rows[0]["summary"] is None
        assert rows[0]["narrative"] is None

    async def test_concurrent_rerun_same_id_conflicts(self, db):
        """A second rerun of an already-running trace_id returns 409, not a race."""
        await _seed_trace(db, trace_id="trace_busy")
        # Simulate an in-flight run holding the cancel-event slot.
        _cancel_events["trace_busy"] = asyncio.Event()
        try:
            async with _client() as client:
                resp = await client.post("/traces/trace_busy/rerun")
            assert resp.status_code == 409
            # The in-flight run's slot must be untouched (not popped by the rejected call).
            assert "trace_busy" in _cancel_events
        finally:
            _cancel_events.pop("trace_busy", None)

    async def test_rerun_missing_trace_404(self, db):
        """Rerunning a non-existent trace_id still 404s (unchanged behavior)."""
        async with _client() as client:
            resp = await client.post("/traces/trace_nope/rerun")
        assert resp.status_code == 404

    async def test_save_trace_upsert_is_idempotent(self, db):
        """Calling _save_trace twice for one id keeps exactly one row."""
        resp = TraceResponse(
            trace_id="trace_idem",
            query="q",
            clusters=[],
            search_metadata=SearchMetadata(),
            created_at="2026-07-16T12:00:00.000000Z",
            space_id=None,
        )
        await _save_trace(resp)
        await _save_trace(resp)

        rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM traces WHERE trace_id = ?", ("trace_idem",)
        )
        assert rows[0]["n"] == 1

    async def test_run_trace_broadcasts_complete(self, db):
        """run_trace announces completion over WS so an aborted client can recover.

        Disabling every discovery signal keeps the run LLM/Chroma-free — it exercises
        the real save + broadcast path without mocking the pipeline internals.
        """
        request = TraceRequest(
            query="anything",
            enable_identifier=False,
            enable_semantic=False,
            enable_entity=False,
            enable_text=False,
            enable_llm_filter=False,
            fuzzy_search=False,
        )
        with patch("laya.pipeline.trace.manager.broadcast", new_callable=AsyncMock) as bc:
            response = await run_trace(request, trace_id="trace_ws")

        completes = [
            c.args[0] for c in bc.call_args_list
            if c.args and c.args[0].get("type") == "trace_complete"
        ]
        assert len(completes) == 1
        assert completes[0]["trace_id"] == "trace_ws" == response.trace_id
