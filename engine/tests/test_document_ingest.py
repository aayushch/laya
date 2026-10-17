# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the document ingest state machine (documents/ingest.py)."""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from laya.documents import ingest, store
from tests.conftest import insert_test_document

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _summary_ok():
    return AsyncMock(return_value={"summary": "## Overview\nRoadmap.", "key_points": [], "identifiers": [], "model": "m"})


@pytest.fixture
def no_autostart():
    """Keep create_* from launching the ingest task so tests drive run_ingest."""
    with patch("laya.documents.ingest._start") as s:
        yield s


@pytest.fixture
def broadcasts():
    with patch("laya.documents.ingest.manager.broadcast", new_callable=AsyncMock) as b:
        yield b


@pytest.mark.asyncio
class TestRunIngest:
    async def test_full_pipeline_sequence(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "roadmap.pdf"
        shutil.copy(FIXTURES / "roadmap.pdf", src)
        row = await ingest.create_from_path("default", str(src))
        assert row["status"] == "queued"
        assert row["storage_mode"] == "reference"

        with patch("laya.documents.ingest.summarize_document", _summary_ok()), \
             patch("laya.pipeline.budget.is_budget_paused", new_callable=AsyncMock, return_value=False):
            await ingest.run_ingest(row["document_id"])

        final = await store.get_document(row["document_id"])
        assert final["status"] == "ready"
        assert final["page_count"] == 2
        assert final["chunk_count"] >= 1
        assert final["summary"].startswith("## Overview")
        assert final["embedding_model"] == "mpnet"
        assert final["error_code"] is None
        assert final["indexed_at"]
        statuses = [c.args[0]["status"] for c in broadcasts.await_args_list if c.args[0].get("type") == "document_updated"]
        for s in ("queued", "parsing", "chunking", "embedding", "linking", "summarizing", "ready"):
            assert s in statuses
        assert statuses.index("parsing") < statuses.index("embedding") < statuses.index("ready")
        mock_document_vectors["delete_where"].assert_awaited_with({"document_id": row["document_id"]})
        mock_document_vectors["embed_batch"].assert_awaited_once()
        ids = mock_document_vectors["embed_batch"].await_args.args[0]
        assert ids[0] == f"{row['document_id']}_chunk_0"
        metas = mock_document_vectors["embed_batch"].await_args.args[2]
        assert metas[0]["content_type"] == "document_chunk"
        assert "card_id" not in metas[0]
        # Summary vector written under the document id.
        mock_document_vectors["embed_document"].assert_awaited_once()
        assert mock_document_vectors["embed_document"].await_args.args[0] == row["document_id"]
        assert mock_document_vectors["embed_document"].await_args.args[2]["content_type"] == "document_summary"
        # Source untouched: still there, same bytes.
        assert src.read_bytes() == (FIXTURES / "roadmap.pdf").read_bytes()
        # FTS rows exist for the chunks.
        rows = await db.execute_fetchall("SELECT COUNT(*) AS c FROM documents_fts WHERE document_id = ?", (row["document_id"],))
        assert rows[0]["c"] == final["chunk_count"]

    async def test_parse_failure_is_fatal(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(width=100, height=100)
        src = tmp_path / "scan.pdf"
        w.write(str(src))
        row = await ingest.create_from_path("default", str(src))
        await ingest.run_ingest(row["document_id"])
        final = await store.get_document(row["document_id"])
        assert final["status"] == "failed"
        assert final["error_code"] == "no_text"
        mock_document_vectors["embed_batch"].assert_not_awaited()

    async def test_embed_failure_keeps_chunks(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\nSome text about PAY-1.")
        row = await ingest.create_from_path("default", str(src))
        mock_document_vectors["embed_batch"].side_effect = RuntimeError("chroma down")
        await ingest.run_ingest(row["document_id"])
        final = await store.get_document(row["document_id"])
        assert final["status"] == "failed"
        assert final["error_code"] == "embed_error"
        assert await store.count_chunks(row["document_id"]) >= 1

    async def test_summary_failure_non_fatal(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\nSome text.")
        row = await ingest.create_from_path("default", str(src))
        with patch("laya.documents.ingest.summarize_document", AsyncMock(side_effect=RuntimeError("llm down"))), \
             patch("laya.pipeline.budget.is_budget_paused", new_callable=AsyncMock, return_value=False):
            await ingest.run_ingest(row["document_id"])
        final = await store.get_document(row["document_id"])
        assert final["status"] == "ready"
        assert final["error_code"] == "summary_error"

    async def test_budget_paused_skips_summary(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\nSome text.")
        row = await ingest.create_from_path("default", str(src))
        summarize = _summary_ok()
        with patch("laya.documents.ingest.summarize_document", summarize), \
             patch("laya.pipeline.budget.is_budget_paused", new_callable=AsyncMock, return_value=True):
            await ingest.run_ingest(row["document_id"])
        final = await store.get_document(row["document_id"])
        assert final["status"] == "ready"
        assert final["error_code"] == "summary_skipped_budget"
        summarize.assert_not_awaited()

    async def test_embed_stage_only_uses_stored_chunks(self, db, mock_document_vectors, broadcasts):
        await insert_test_document(db, document_id="doc_e", source_path="/nowhere/gone.pdf", summary="old summary")
        summarize = _summary_ok()
        with patch("laya.documents.ingest.summarize_document", summarize):
            await ingest.run_ingest("doc_e", ("embed",))
        final = await store.get_document("doc_e")
        assert final["status"] == "ready"
        assert final["embedding_model"] == "mpnet"
        mock_document_vectors["embed_batch"].assert_awaited_once()
        summarize.assert_not_awaited()
        # The existing summary vector is re-upserted with current metadata.
        mock_document_vectors["embed_document"].assert_awaited_once()

    async def test_reprocess_all_with_missing_source_keeps_ready(self, db, mock_document_vectors, broadcasts):
        await insert_test_document(db, document_id="doc_m", source_path="/nowhere/gone.pdf")
        await ingest.run_ingest("doc_m", ingest.ALL_STAGES)
        final = await store.get_document("doc_m")
        assert final["status"] == "ready"
        assert final["error_code"] == "source_missing"
        assert await store.count_chunks("doc_m") == 1

    async def test_cancellation_marks_interrupted(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\nSome text.")
        row = await ingest.create_from_path("default", str(src))
        started = asyncio.Event()

        async def slow_embed(*a, **k):
            started.set()
            await asyncio.sleep(10)

        mock_document_vectors["embed_batch"].side_effect = slow_embed
        task = asyncio.create_task(ingest.run_ingest(row["document_id"]))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        final = await store.get_document(row["document_id"])
        assert final["status"] == "failed"
        assert final["error_code"] == "interrupted"

    async def test_recover_requeues_stalled_and_interrupted(self, db, mock_document_vectors, broadcasts):
        await insert_test_document(db, document_id="doc_s", status="embedding")
        await insert_test_document(db, document_id="doc_i", status="failed")
        await store.set_status("doc_i", "failed", error="x", error_code="interrupted")
        await insert_test_document(db, document_id="doc_ok", status="ready")
        with patch("laya.documents.ingest._start") as start:
            await ingest.recover_stalled_documents()
        assert {c.args[0] for c in start.call_args_list} == {"doc_s", "doc_i"}
        assert (await store.get_document("doc_s"))["status"] == "queued"


@pytest.mark.asyncio
class TestCreate:
    async def test_from_path_records_reference_and_no_copy(self, db, tmp_path, no_autostart, broadcasts):
        from laya.config import DOCUMENTS_DIR

        src = tmp_path / "notes.docx"
        shutil.copy(FIXTURES / "notes.docx", src)
        before = set(DOCUMENTS_DIR.glob("*")) if DOCUMENTS_DIR.exists() else set()
        row = await ingest.create_from_path("default", str(src), title="My Notes")
        assert row["source_path"] == str(src.absolute())
        assert row["source_mtime"] == pytest.approx(src.stat().st_mtime)
        assert row["stored_path"] is None
        assert row["title"] == "My Notes"
        assert row["file_type"] == "docx"
        after = set(DOCUMENTS_DIR.glob("*")) if DOCUMENTS_DIR.exists() else set()
        assert after == before

    async def test_from_path_errors(self, db, tmp_path, no_autostart, broadcasts):
        with pytest.raises(ingest.DocumentError) as e:
            await ingest.create_from_path("default", str(tmp_path / "nope.pdf"))
        assert e.value.status_code == 404
        bad = tmp_path / "a.exe"
        bad.write_bytes(b"x")
        with pytest.raises(ingest.DocumentError) as e:
            await ingest.create_from_path("default", str(bad))
        assert e.value.status_code == 415
        big = tmp_path / "big.txt"
        big.write_text("y" * 10)
        with patch("laya.documents.ingest.max_file_bytes", return_value=5), pytest.raises(ingest.DocumentError) as e:
            await ingest.create_from_path("default", str(big))
        assert e.value.status_code == 413

    async def test_duplicate_in_space(self, db, tmp_path, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\ntext")
        first = await ingest.create_from_path("default", str(src))
        with pytest.raises(ingest.DocumentError) as e:
            await ingest.create_from_path("default", str(src))
        assert e.value.status_code == 409
        assert e.value.extra["document_id"] == first["document_id"]
        # Same file in another space is fine.
        other = await ingest.create_from_path("space_b", str(src))
        assert other["document_id"] != first["document_id"]

    async def test_from_bytes_keeps_copy(self, db, no_autostart, broadcasts):
        from laya.config import DOCUMENTS_DIR

        async def stream():
            yield b"# Title\n\n"
            yield b"body text"

        row = await ingest.create_from_bytes("default", "upload.md", stream(), content_type="text/markdown")
        assert row["storage_mode"] == "copy"
        stored = Path(row["stored_path"])
        assert stored.parent == DOCUMENTS_DIR
        assert stored.read_bytes() == b"# Title\n\nbody text"
        assert row["size_bytes"] == len(b"# Title\n\nbody text")

    async def test_from_bytes_too_large_leaves_no_file(self, db, no_autostart, broadcasts):
        from laya.config import DOCUMENTS_DIR

        async def stream():
            yield b"x" * 10

        with patch("laya.documents.ingest.max_file_bytes", return_value=5), pytest.raises(ingest.DocumentError) as e:
            await ingest.create_from_bytes("default", "a.txt", stream())
        assert e.value.status_code == 413
        assert not list(DOCUMENTS_DIR.glob(".upload-*"))


@pytest.mark.asyncio
class TestResyncRelocateDelete:
    async def test_resync_unchanged_and_changed(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\noriginal text")
        row = await ingest.create_from_path("default", str(src))
        result = await ingest.resync(row["document_id"])
        assert result["status"] == "unchanged"
        src.write_text("# T\n\nchanged text")
        with patch("laya.documents.ingest._start") as start:
            result = await ingest.resync(row["document_id"])
        assert result["status"] == "queued"
        start.assert_called_once()
        updated = await store.get_document(row["document_id"])
        assert updated["sha256"] != row["sha256"]
        assert store.source_state(updated) == "ok"

    async def test_source_state_transitions(self, db, tmp_path, no_autostart, broadcasts):
        import os

        src = tmp_path / "a.md"
        src.write_text("# T\n\ntext")
        row = await ingest.create_from_path("default", str(src))
        assert store.source_state(row) == "ok"
        os.utime(src, (src.stat().st_atime, src.stat().st_mtime + 100))
        assert store.source_state(row) == "changed"
        moved = tmp_path / "moved.md"
        src.rename(moved)
        assert store.source_state(row) == "missing"
        with pytest.raises(ingest.DocumentError) as e:
            await ingest.resync(row["document_id"])
        assert e.value.error_code == "source_missing"
        # Relocate verifies the hash.
        other = tmp_path / "other.md"
        other.write_text("different")
        with pytest.raises(ingest.DocumentError) as e:
            await ingest.relocate(row["document_id"], str(other))
        assert e.value.status_code == 409
        updated = await ingest.relocate(row["document_id"], str(moved))
        assert updated["source_path"] == str(moved.absolute())
        assert store.source_state(updated) == "ok"

    async def test_delete_cascade_reference_leaves_source(self, db, tmp_path, mock_document_vectors, no_autostart, broadcasts):
        src = tmp_path / "a.md"
        src.write_text("# T\n\ntext")
        row = await ingest.create_from_path("default", str(src))
        await ingest.run_ingest(row["document_id"], ("parse", "chunk", "link"))
        assert await ingest.delete_document_cascade(row["document_id"]) is True
        assert await store.get_document(row["document_id"]) is None
        assert await store.count_chunks(row["document_id"]) == 0
        fts = await db.execute_fetchall("SELECT COUNT(*) AS c FROM documents_fts WHERE document_id = ?", (row["document_id"],))
        assert fts[0]["c"] == 0
        mock_document_vectors["delete_where"].assert_awaited_with({"document_id": row["document_id"]})
        assert src.exists()
        assert await ingest.delete_document_cascade("doc_unknown") is False
        types = [c.args[0]["type"] for c in broadcasts.await_args_list]
        assert "document_deleted" in types

    async def test_delete_cascade_copy_unlinks(self, db, mock_document_vectors, no_autostart, broadcasts):
        async def stream():
            yield b"# T\n\ntext"

        row = await ingest.create_from_bytes("default", "u.md", stream())
        stored = Path(row["stored_path"])
        assert stored.exists()
        await ingest.delete_document_cascade(row["document_id"])
        assert not stored.exists()
