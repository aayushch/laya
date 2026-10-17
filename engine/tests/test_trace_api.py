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


@pytest.mark.asyncio
class TestTraceDocuments:
    """Corpus documents ride on trace clusters and round-trip through the DB."""

    async def test_semantic_search_filters_cards_and_drops_hits_without_card_id(self, db):
        from laya.pipeline.trace import _semantic_search

        hits = [
            {"id": "card_1", "document": "x", "metadata": {"card_id": "card_1", "entity_id": "e1"}, "distance": 0.1},
            {"id": "doc_1_chunk_0", "document": "y", "metadata": {"document_id": "doc_1"}, "distance": 0.1},
        ]
        with patch("laya.pipeline.trace.memory_search", new_callable=AsyncMock, return_value=hits) as ms:
            out = await _semantic_search("q", "default", n=5)
        assert [o["card_id"] for o in out] == ["card_1"]
        assert ms.await_args.kwargs["where"]["$and"][0] == {"content_type": {"$nin": ["document_chunk", "document_summary"]}}

    async def test_cluster_documents_and_round_trip(self, db):
        from laya.api.cards_common import _row_to_card
        from laya.api.cards_api import CARD_SELECT_COLUMNS
        from laya.api.trace_api import _reconstruct_trace
        from laya.documents import store
        from laya.models.trace import TraceDocumentRef, TraceEntity
        from laya.pipeline.trace import _build_clusters, _document_links_for
        from laya.models.trace import TraceRequest
        from tests.conftest import insert_test_card, insert_test_document

        await insert_test_card(db, card_id="card_a", event_id="evt_a", entity_id="jira:ticket:PAY-1", space_id="default")
        await insert_test_document(db, document_id="doc_a", title="PRD")
        await store.replace_links("doc_a", [{"entity_id": "jira:ticket:PAY-1", "link_method": "identifier", "confidence": 1.0, "matched_text": "PAY-1", "first_page": 2}])
        await insert_test_document(db, document_id="doc_loose", title="Loose")
        await store.replace_links("doc_loose", [{"entity_id": "jira:ticket:OTHER-9", "link_method": "identifier", "confidence": 1.0, "matched_text": "OTHER-9"}])

        rows = await db.execute_fetchall(
            f"SELECT {CARD_SELECT_COLUMNS} FROM action_cards c LEFT JOIN events e ON c.event_id = e.event_id "
            "LEFT JOIN spaces s ON c.space_id = s.space_id WHERE c.card_id = 'card_a'"
        )
        cards = [_row_to_card(rows[0])]
        request = TraceRequest(query="trace OTHER-9", space_id="default")
        doc_links = await _document_links_for(request, cards, [])
        assert [d.document_id for d in doc_links["jira:ticket:PAY-1"]] == ["doc_a"]
        assert [d.document_id for d in doc_links[""]] == ["doc_loose"]

        entity_map = {"jira:ticket:PAY-1": TraceEntity(entity_id="jira:ticket:PAY-1", title="PAY-1", platform="jira")}
        clusters = _build_clusters(cards, entity_map, [], doc_links)
        assert len(clusters) == 1
        assert [d.document_id for d in clusters[0].documents] == ["doc_a"]
        assert clusters[0].documents[0].first_page == 2

        resp = TraceResponse(
            trace_id="trace_docs", query=request.query, clusters=clusters,
            search_metadata=SearchMetadata(), created_at="2026-07-16 12:00:00", space_id="default",
            documents=doc_links[""],
        )
        await _save_trace(resp)
        row = (await db.execute_fetchall("SELECT * FROM traces WHERE trace_id = 'trace_docs'"))[0]
        rebuilt = await _reconstruct_trace(db, row)
        assert [d.document_id for d in rebuilt.clusters[0].documents] == ["doc_a"]
        assert isinstance(rebuilt.clusters[0].documents[0], TraceDocumentRef)
        assert [d.document_id for d in rebuilt.documents] == ["doc_loose"]
