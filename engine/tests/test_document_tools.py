# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Corpus document chat tools and hybrid chunk search."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from laya.documents import search as document_search
from laya.llm.tools import document_tools
from laya.llm.tools.executor import execute_tool
from tests.conftest import insert_test_document


def _chunks(n: int):
    return [
        {"text": f"Page {i + 1} talks about the payment service migration part {i}.", "page_start": i + 1, "page_end": i + 1, "heading": "Plan"}
        for i in range(n)
    ]


@pytest.mark.asyncio
class TestListDocuments:
    async def test_lists_ready_only_by_default(self, db):
        await insert_test_document(db, document_id="doc_r", title="Ready", summary="A summary.")
        await insert_test_document(db, document_id="doc_q", title="Queued", status="queued")
        out = await document_tools.list_documents()
        assert [d["document_id"] for d in out["documents"]] == ["doc_r"]
        assert out["documents"][0]["summary_preview"] == "A summary."
        assert out["total"] == 1 and out["has_more"] is False

    async def test_space_filter(self, db):
        await insert_test_document(db, document_id="doc_a", space_id="a")
        await insert_test_document(db, document_id="doc_b", space_id="b")
        out = await document_tools.list_documents(space_id="b")
        assert [d["document_id"] for d in out["documents"]] == ["doc_b"]


@pytest.mark.asyncio
class TestSearchDocuments:
    async def test_keyword_hits_with_page(self, db):
        await insert_test_document(db, document_id="doc_k", title="Roadmap", chunks=_chunks(3))
        await insert_test_document(db, document_id="doc_other", title="Other", chunks=[{"text": "Unrelated newsletter", "page_start": 1, "page_end": 1}])
        with patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]):
            out = await document_tools.search_documents("payment migration")
        assert out["count"] >= 1
        assert all(r["document_id"] == "doc_k" for r in out["results"])
        assert out["results"][0]["page_start"] == 1
        assert out["results"][0]["title"] == "Roadmap"

    async def test_document_id_restricts(self, db):
        await insert_test_document(db, document_id="doc_1", chunks=_chunks(2))
        await insert_test_document(db, document_id="doc_2", chunks=_chunks(2))
        with patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]):
            out = await document_tools.search_documents("payment", document_id="doc_2")
        assert {r["document_id"] for r in out["results"]} == {"doc_2"}

    async def test_vector_and_keyword_fuse_on_chunk_id(self, db):
        await insert_test_document(db, document_id="doc_v", title="Vec", chunks=_chunks(2))
        vec_hit = {
            "id": "doc_v_chunk_1",
            "document": "Page 2 talks about ...",
            "metadata": {"content_type": "document_chunk", "document_id": "doc_v", "title": "Vec",
                         "page_start": 2, "page_end": 2, "heading": "Plan", "chunk_index": 1},
            "distance": 0.2,
        }
        with patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[vec_hit]) as ms:
            hits = await document_search.hybrid_chunk_search("payment", None, n=10)
        where = ms.await_args.kwargs["where"]
        assert where == {"content_type": {"$in": ["document_chunk", "document_summary"]}}
        ids = [h["id"] for h in hits]
        assert ids.count("doc_v_chunk_1") == 1
        assert ids[0] == "doc_v_chunk_1"  # present in both lists, so ranked first

    async def test_summary_hit_shape(self, db):
        summary_hit = {
            "id": "doc_s", "document": "Overview...",
            "metadata": {"content_type": "document_summary", "document_id": "doc_s", "title": "S"},
            "distance": 0.1,
        }
        with patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[summary_hit]):
            hits = await document_search.vector_chunk_search("q", "default", document_ids=["doc_s"])
        assert hits[0]["chunk_id"] is None and hits[0]["metadata"]["is_summary"] is True

    async def test_empty_query(self, db):
        out = await document_tools.search_documents("  ")
        assert out["count"] == 0 and "error" in out

    async def test_like_fallback_matches_fts(self, db):
        await insert_test_document(db, document_id="doc_f", title="Fallback", chunks=_chunks(2))
        with patch("laya.documents.search.memory_search", new_callable=AsyncMock, return_value=[]):
            fts = await document_search.keyword_chunk_search("migration", None, n=10)
            with patch("laya.retrieval.fts_ready", return_value=False):
                like = await document_search.keyword_chunk_search("migration", None, n=10)
        assert {h["id"] for h in fts} == {h["id"] for h in like}


@pytest.mark.asyncio
class TestReadDocument:
    async def test_default_first_chunks_with_summary(self, db):
        await insert_test_document(db, document_id="doc_r", chunks=_chunks(12), summary="Sum", page_count=12)
        out = await document_tools.read_document("doc_r")
        assert out["summary"] == "Sum"
        assert len(out["chunks"]) == 8
        assert out["has_more"] is True
        assert out["next_chunk_index"] == 8
        assert out["total_chunks"] == 12

    async def test_page_range(self, db):
        await insert_test_document(db, document_id="doc_p", chunks=_chunks(10), page_count=10)
        out = await document_tools.read_document("doc_p", page_from=3, page_to=4)
        assert [c["page_start"] for c in out["chunks"]] == [3, 4]
        assert "summary" not in out

    async def test_chunk_range_and_cap(self, db):
        await insert_test_document(db, document_id="doc_c", chunks=[{"text": "y" * 3000, "page_start": i, "page_end": i} for i in range(1, 6)])
        out = await document_tools.read_document("doc_c", chunk_from=0, chunk_to=4, max_chars=7000)
        assert len(out["chunks"]) == 2
        assert out["has_more"] is True
        assert len(json.dumps(out)) < 12000

    async def test_unknown_and_wrong_space(self, db):
        assert "error" in await document_tools.read_document("doc_nope")
        await insert_test_document(db, document_id="doc_s", space_id="a")
        assert "error" in await document_tools.read_document("doc_s", space_id="b")

    async def test_executor_routes_document_tools(self, db):
        await insert_test_document(db, document_id="doc_x", title="Exec")
        result = json.loads(await execute_tool("list_documents", {}, space_id="default"))
        assert result["documents"][0]["document_id"] == "doc_x"
        result = json.loads(await execute_tool("read_document", {"document_id": "doc_x"}, space_id="default"))
        assert result["title"] == "Exec"
