# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Corpus documents REST API (api/documents_api.py)."""

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.documents import ingest, store
from tests.conftest import insert_test_card, insert_test_document

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _client():
    from laya.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def no_ingest():
    """Keep create_* from launching the ingest task; HTTP tests check the row only."""
    with patch("laya.documents.ingest._start") as s, \
         patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
        yield s


@pytest.mark.asyncio
class TestCreate:
    async def test_multipart_upload_keeps_copy(self, db, no_ingest):
        from laya.config import DOCUMENTS_DIR

        async with _client() as c:
            resp = await c.post(
                "/documents",
                files={"file": ("roadmap.pdf", (FIXTURES / "roadmap.pdf").read_bytes(), "application/pdf")},
                data={"space_id": "default", "title": "Roadmap"},
            )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["storage_mode"] == "copy"
        assert body["source_state"] == "copy"
        assert body["title"] == "Roadmap"
        row = await store.get_document(body["document_id"])
        assert Path(row["stored_path"]).parent == DOCUMENTS_DIR
        assert Path(row["stored_path"]).exists()
        no_ingest.assert_called_once()

    async def test_from_path_makes_no_copy(self, db, tmp_path, no_ingest):
        from laya.config import DOCUMENTS_DIR

        src = tmp_path / "roadmap.pdf"
        shutil.copy(FIXTURES / "roadmap.pdf", src)
        original = src.read_bytes()
        before = sorted(DOCUMENTS_DIR.glob("*"))
        async with _client() as c:
            resp = await c.post("/documents/from-path", json={"path": str(src), "space_id": "default"})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["storage_mode"] == "reference"
        assert body["source_state"] == "ok"
        assert body["source_path"] == str(src)
        assert sorted(DOCUMENTS_DIR.glob("*")) == before
        assert src.read_bytes() == original
        row = await store.get_document(body["document_id"])
        assert row["source_mtime"] == pytest.approx(src.stat().st_mtime)

    async def test_from_path_404(self, db, tmp_path, no_ingest):
        async with _client() as c:
            resp = await c.post("/documents/from-path", json={"path": str(tmp_path / "nope.pdf")})
        assert resp.status_code == 404

    async def test_unsupported_415(self, db, no_ingest):
        async with _client() as c:
            resp = await c.post("/documents", files={"file": ("x.exe", b"abc", "application/octet-stream")})
        assert resp.status_code == 415
        assert resp.json()["detail"]["error_code"] == "unsupported"

    async def test_too_large_413(self, db, no_ingest):
        with patch("laya.documents.ingest.max_file_bytes", return_value=4):
            async with _client() as c:
                resp = await c.post("/documents", files={"file": ("x.txt", b"abcdefgh", "text/plain")})
        assert resp.status_code == 413

    async def test_duplicate_409_with_existing_id(self, db, no_ingest):
        async with _client() as c:
            first = await c.post("/documents", files={"file": ("a.txt", b"same bytes", "text/plain")})
            second = await c.post("/documents", files={"file": ("b.txt", b"same bytes", "text/plain")})
        assert second.status_code == 409
        assert second.json()["detail"]["document_id"] == first.json()["document_id"]


@pytest.mark.asyncio
class TestRead:
    async def test_list_get_chunks(self, db):
        await insert_test_document(db, document_id="doc_1", title="One", space_id="default",
                                   chunks=[{"text": "p1", "page_start": 1, "page_end": 1}, {"text": "p2", "page_start": 2, "page_end": 2}],
                                   summary="S", page_count=2, embedding_model="mpnet")
        await insert_test_document(db, document_id="doc_2", title="Two", space_id="other")
        with patch("laya.api.documents_api.get_embedding_info", return_value={"backend": "minilm"}):
            async with _client() as c:
                listing = (await c.get("/documents", params={"space_id": "default"})).json()
                assert listing["total"] == 1
                assert listing["documents"][0]["document_id"] == "doc_1"
                assert listing["documents"][0]["needs_reembed"] is True
                assert listing["documents"][0]["source_state"] == "missing"
                search = (await c.get("/documents", params={"q": "Two"})).json()
                assert [d["document_id"] for d in search["documents"]] == ["doc_2"]
                doc = (await c.get("/documents/doc_1")).json()
                assert doc["summary"] == "S" and doc["entity_links"] == []
                chunks = (await c.get("/documents/doc_1/chunks", params={"page_from": 2, "page_to": 2})).json()
                assert chunks["total"] == 1 and chunks["chunks"][0]["text"] == "p2"
                assert (await c.get("/documents/doc_missing")).status_code == 404
                types = (await c.get("/documents/supported-types")).json()
                assert "pdf" in types["extensions"] and types["max_file_mb"] == 50

    async def test_related_and_by_entity(self, db):
        await insert_test_card(db, card_id="card_a", event_id="evt_a", entity_id="jira:ticket:PAY-1", space_id="default")
        await insert_test_document(db, document_id="doc_l", title="Linked")
        await store.replace_links("doc_l", [{"entity_id": "jira:ticket:PAY-1", "link_method": "identifier", "confidence": 1.0, "matched_text": "PAY-1"}])
        async with _client() as c:
            related = (await c.get("/documents/doc_l/related")).json()
            assert related["entities"][0]["entity_id"] == "jira:ticket:PAY-1"
            assert related["entities"][0]["card_count"] == 1
            assert [card["card_id"] for card in related["cards"]] == ["card_a"]
            by_entity = (await c.get("/documents/by-entity", params={"entity_id": "jira:ticket:PAY-1"})).json()
            assert by_entity[0]["document_id"] == "doc_l" and by_entity[0]["link_method"] == "identifier"
            for_card = (await c.get("/cards/card_a/related-documents")).json()
            assert [d["document_id"] for d in for_card] == ["doc_l"]
            assert (await c.get("/cards/card_none/related-documents")).status_code == 404


@pytest.mark.asyncio
class TestUpdate:
    async def test_rename_updates_fts_and_vectors(self, db):
        await insert_test_document(db, document_id="doc_r", title="Old Name", chunks=[{"text": "body text", "page_start": 1, "page_end": 1}])
        with patch("laya.api.documents_api.update_document_metadata", new_callable=AsyncMock) as patch_meta, \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
            async with _client() as c:
                resp = await c.patch("/documents/doc_r", json={"title": "New Name"})
        assert resp.status_code == 200 and resp.json()["title"] == "New Name"
        patch_meta.assert_awaited_once_with("doc_r", {"title": "New Name"})
        rows = await db.execute_fetchall("SELECT title FROM documents_fts WHERE document_id = 'doc_r'")
        assert rows[0]["title"] == "New Name"

    async def test_reprocess_stages(self, db):
        await insert_test_document(db, document_id="doc_p", source_path="/nowhere/x.pdf")
        with patch("laya.documents.ingest._start") as start:
            async with _client() as c:
                assert (await c.post("/documents/doc_p/reprocess", params={"stage": "embed"})).status_code == 202
                start.assert_called_with("doc_p", ("embed",))
                missing = await c.post("/documents/doc_p/reprocess", params={"stage": "all"})
                assert missing.status_code == 409
                assert missing.json()["detail"]["error_code"] == "source_missing"
                assert (await c.post("/documents/doc_p/reprocess", params={"stage": "bogus"})).status_code == 422
                assert (await c.post("/documents/doc_none/reprocess")).status_code == 404

    async def test_resync_and_relocate(self, db, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# T\n\ntext")
        with patch("laya.documents.ingest._start"), \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
            row = await ingest.create_from_path("default", str(src))
            did = row["document_id"]
            async with _client() as c:
                unchanged = await c.post(f"/documents/{did}/resync")
                assert unchanged.status_code == 200 and unchanged.json()["status"] == "unchanged"
                src.write_text("# T\n\nchanged")
                changed = await c.post(f"/documents/{did}/resync")
                assert changed.status_code == 200 and changed.json()["status"] == "queued"
                moved = tmp_path / "b.md"
                src.rename(moved)
                assert (await c.get(f"/documents/{did}")).json()["source_state"] == "missing"
                assert (await c.post(f"/documents/{did}/resync")).status_code == 409
                wrong = tmp_path / "c.md"
                wrong.write_text("other")
                assert (await c.post(f"/documents/{did}/relocate", json={"path": str(wrong)})).status_code == 409
                ok = await c.post(f"/documents/{did}/relocate", json={"path": str(moved)})
                assert ok.status_code == 200 and ok.json()["source_state"] == "ok"

    async def test_delete(self, db, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# T\n\ntext")
        with patch("laya.documents.ingest._start"), \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock), \
             patch("laya.documents.ingest.delete_where", new_callable=AsyncMock):
            row = await ingest.create_from_path("default", str(src))
            async with _client() as c:
                assert (await c.delete(f"/documents/{row['document_id']}")).json()["status"] == "deleted"
                assert (await c.delete(f"/documents/{row['document_id']}")).status_code == 404
        assert src.exists()


async def _add_space(db, space_id, name):
    await db.execute("INSERT INTO spaces (space_id, name) VALUES (?, ?)", (space_id, name))
    await db.commit()


@pytest.mark.asyncio
class TestMove:
    """POST /documents/{id}/move: preview, move, and the cases that refuse a move."""

    async def _setup(self, db):
        await _add_space(db, "space_b", "Payments")
        # PAY-1 has a card in both spaces, PAY-2 only in default, PAY-3 only in space_b.
        await insert_test_card(db, card_id="c1", event_id="e1", entity_id="jira:ticket:PAY-1", space_id="default")
        await insert_test_card(db, card_id="c1b", event_id="e1b", entity_id="jira:ticket:PAY-1", space_id="space_b")
        await insert_test_card(db, card_id="c2", event_id="e2", entity_id="jira:ticket:PAY-2", space_id="default")
        await insert_test_card(db, card_id="c3", event_id="e3", entity_id="jira:ticket:PAY-3", space_id="space_b")
        await insert_test_document(db, document_id="doc_m", chunks=[
            {"text": "Rollout covers PAY-1, PAY-2 and PAY-3 for the payment service.", "page_start": 1, "page_end": 1},
        ])
        await store.replace_links("doc_m", [
            {"entity_id": "jira:ticket:PAY-1", "link_method": "identifier", "confidence": 1.0, "matched_text": "PAY-1"},
            {"entity_id": "jira:ticket:PAY-2", "link_method": "identifier", "confidence": 1.0, "matched_text": "PAY-2"},
        ])
        await db.execute(
            "INSERT INTO chat_conversations (conversation_id, title, space_id, created_at, updated_at, document_ids) "
            "VALUES ('conv_1', 'Chat', 'default', '2026-10-19 00:00:00', '2026-10-19 00:00:00', ?)",
            (store.canonical_document_ids(["doc_m"]),),
        )
        await db.commit()

    async def test_dry_run_describes_without_writing(self, db):
        await self._setup(db)
        with patch("laya.documents.ingest.update_document_metadata", new_callable=AsyncMock) as patch_meta:
            async with _client() as c:
                resp = await c.post("/documents/doc_m/move", json={"space_id": "space_b", "dry_run": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["links_kept"] == ["jira:ticket:PAY-1"]
        assert body["links_removed"] == ["jira:ticket:PAY-2"]
        assert body["links_added"] == ["jira:ticket:PAY-3"]
        assert body["attached_chat_count"] == 1
        notes = " ".join(body["notes"])
        assert "unlinked" in notes and "PAY-2" in notes and "newly linked" in notes and "PAY-3" in notes
        assert "no longer search its passages" in notes
        patch_meta.assert_not_awaited()
        assert (await store.get_document("doc_m"))["space_id"] == "default"

    async def test_move_updates_row_links_and_vectors(self, db):
        await self._setup(db)
        with patch("laya.documents.ingest.update_document_metadata", new_callable=AsyncMock) as patch_meta, \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
            async with _client() as c:
                resp = await c.post("/documents/doc_m/move", json={"space_id": "space_b"})
        assert resp.status_code == 200
        assert resp.json()["document"]["space_id"] == "space_b"
        assert (await store.get_document("doc_m"))["space_id"] == "space_b"
        assert sorted(l["entity_id"] for l in await store.links_for_document("doc_m")) == [
            "jira:ticket:PAY-1", "jira:ticket:PAY-3",
        ]
        patch_meta.assert_awaited_once()
        doc_id, fields = patch_meta.await_args.args
        assert doc_id == "doc_m" and fields["space_id"] == "space_b"
        assert "PAY-3" in fields["entity_refs"] and "PAY-2" not in fields["entity_refs"]
        # The chat attachment is untouched.
        rows = await db.execute_fetchall("SELECT document_ids FROM chat_conversations WHERE conversation_id = 'conv_1'")
        assert store.parse_document_ids(rows[0]["document_ids"]) == ["doc_m"]

    async def test_vector_patch_failure_starts_reembed(self, db):
        await self._setup(db)
        with patch("laya.documents.ingest.update_document_metadata", new_callable=AsyncMock, side_effect=RuntimeError("chroma down")), \
             patch("laya.documents.ingest._start") as start, \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
            async with _client() as c:
                resp = await c.post("/documents/doc_m/move", json={"space_id": "space_b"})
        assert resp.status_code == 200
        start.assert_called_once_with("doc_m", ("embed",))

    async def test_refusals(self, db):
        await self._setup(db)
        await insert_test_document(db, document_id="doc_busy", status="summarizing", sha256="sha_busy")
        await insert_test_document(db, document_id="doc_dup", space_id="space_b", sha256="sha_doc_m")
        async with _client() as c:
            busy = await c.post("/documents/doc_busy/move", json={"space_id": "space_b", "dry_run": True})
            assert busy.status_code == 409 and busy.json()["detail"]["error_code"] == "processing"
            dup = await c.post("/documents/doc_m/move", json={"space_id": "space_b"})
            assert dup.status_code == 409 and dup.json()["detail"]["error_code"] == "duplicate"
            same = await c.post("/documents/doc_m/move", json={"space_id": "default"})
            assert same.status_code == 400 and same.json()["detail"]["error_code"] == "same_space"
            nospace = await c.post("/documents/doc_m/move", json={"space_id": "space_x"})
            assert nospace.status_code == 404
            assert (await c.post("/documents/doc_none/move", json={"space_id": "space_b"})).status_code == 404
        assert (await store.get_document("doc_m"))["space_id"] == "default"

    async def test_reprocess_refused_while_moving(self, db):
        await insert_test_document(db, document_id="doc_mv")
        ingest._moving.add("doc_mv")
        try:
            async with _client() as c:
                resp = await c.post("/documents/doc_mv/reprocess", params={"stage": "embed"})
            assert resp.status_code == 409 and resp.json()["detail"]["error_code"] == "moving"
        finally:
            ingest._moving.discard("doc_mv")


@pytest.mark.asyncio
class TestEndToEnd:
    async def test_upload_runs_ingest(self, db, tmp_path, mock_document_vectors):
        """One real run of the ingest task from the API with the vector and LLM calls mocked."""
        src = tmp_path / "roadmap.pdf"
        shutil.copy(FIXTURES / "roadmap.pdf", src)
        summary = AsyncMock(return_value={"summary": "## Overview\nRoadmap.", "key_points": [], "identifiers": [], "model": "m"})
        with patch("laya.documents.ingest.summarize_document", summary), \
             patch("laya.pipeline.budget.is_budget_paused", new_callable=AsyncMock, return_value=False), \
             patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock):
            async with _client() as c:
                resp = await c.post("/documents/from-path", json={"path": str(src)})
                did = resp.json()["document_id"]
                task = ingest._running[did]
                await task
                doc = (await c.get(f"/documents/{did}")).json()
        assert doc["status"] == "ready"
        assert doc["page_count"] == 2
        assert doc["summary"].startswith("## Overview")
        assert doc["title"] == "roadmap"
