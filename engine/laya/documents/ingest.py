# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Corpus document lifecycle: create, ingest, reprocess, re-sync, delete.

A document row is created in status ``queued`` and a tracked task runs
``run_ingest`` through the stages parsing -> chunking -> embedding -> linking
-> summarizing -> ready. Each transition is written to the row and broadcast
as ``document_updated`` over the WebSocket. Parsing and chunking failures are
fatal (status ``failed``); linking and summarising failures only record an
error code on a document that still becomes ``ready``.

Reference-mode documents are read in place at ``source_path`` and never
written to. Only multipart uploads keep a copy under ``DOCUMENTS_DIR``.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import structlog

from laya.api.websocket import manager
from laya.config import DEFAULT_SETTINGS, DOCUMENTS_DIR, load_settings
from laya.db.chromadb_store import (
    delete_where,
    embed_batch,
    embed_document,
    get_embedding_info,
)
from laya.db.timeutil import db_now
from laya.documents import store
from laya.documents.chunking import chunk_units
from laya.documents.linking import entity_refs_csv, extract_identifiers, resolve_entity_links
from laya.documents.parsers import ParseError, file_type_for, for_extension, supported_extensions
from laya.documents.parsers.base import TextUnit
from laya.documents.summary import summarize_document
from laya.tasks import create_task

log = structlog.get_logger()

ALL_STAGES = ("parse", "chunk", "embed", "link", "summarize")

# One document embeds at a time so a large drop cannot starve card emits of
# the encoder; the batch loop inside embed_batch yields between slices.
_ingest_sem = asyncio.Semaphore(1)
_running: dict[str, asyncio.Task] = {}


class DocumentError(Exception):
    """A create/reprocess request the API maps to an HTTP status."""

    def __init__(self, status_code: int, message: str, error_code: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        self.extra = extra


# ---------------------------------------------------------------------------
# Settings and helpers
# ---------------------------------------------------------------------------


def _doc_settings() -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["documents"]
    cfg = load_settings().get("documents", {})
    return {
        "max_file_mb": int(cfg.get("max_file_mb", defaults["max_file_mb"])),
        "embed_batch_size": int(cfg.get("embed_batch_size", defaults["embed_batch_size"])),
    }


def max_file_bytes() -> int:
    return _doc_settings()["max_file_mb"] * 1024 * 1024


def _sanitize_ext(filename: str) -> str:
    """Lower-case alphanumeric extension (max 8 chars) or ''."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    return "".join(c for c in suffix if c.isalnum())[:8]


def _validate_extension(filename: str) -> tuple[str, str]:
    """Return ``(ext, file_type)`` or raise 415."""
    ext = _sanitize_ext(filename)
    file_type = file_type_for(ext) if ext else None
    if not file_type:
        raise DocumentError(
            415,
            f"Unsupported file type '.{ext or '?'}'. Supported: {', '.join(supported_extensions())}",
            "unsupported",
        )
    return ext, file_type


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _new_id() -> str:
    return f"doc_{uuid.uuid4().hex[:12]}"


def _title_from(filename: str, title: str | None) -> str:
    t = (title or "").strip()
    return t or (Path(filename).stem or filename)


async def _broadcast(row: dict[str, Any], progress: dict[str, Any] | None = None) -> None:
    try:
        await manager.broadcast({
            "type": "document_updated",
            "document_id": row["document_id"],
            "space_id": row.get("space_id"),
            "status": row.get("status"),
            "title": row.get("title"),
            "progress": progress,
            "error": row.get("error"),
            "error_code": row.get("error_code"),
        })
    except Exception as e:
        log.debug("document_broadcast_failed", document_id=row.get("document_id"), error=str(e))


async def _transition(document_id: str, status: str, **fields: Any) -> dict[str, Any]:
    await store.set_status(document_id, status, **fields)
    row = await store.get_document(document_id)
    if row:
        await _broadcast(row, {"stage": status} if status not in store.TERMINAL_STATUSES else None)
    return row or {"document_id": document_id, "status": status}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_from_bytes(
    space_id: str,
    filename: str,
    stream: AsyncIterator[bytes],
    *,
    title: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Create a ``storage_mode='copy'`` document from a multipart body.

    The body is hashed and size-checked while streaming into a temp file in
    ``DOCUMENTS_DIR``; the temp file is renamed to its final name only after
    the row is inserted. Raises ``DocumentError`` for 413/415/409.
    """
    ext, file_type = _validate_extension(filename)
    cap = max_file_bytes()
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DOCUMENTS_DIR / f".upload-{uuid.uuid4().hex}.{ext}"
    h = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in stream:
                size += len(chunk)
                if size > cap:
                    raise DocumentError(413, f"File exceeds the {cap // (1024 * 1024)} MB limit", "too_large")
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise DocumentError(400, "The uploaded file is empty", "parse_error")
        sha = h.hexdigest()
        existing = await store.find_by_hash(space_id, sha)
        if existing:
            raise DocumentError(409, "This file is already in the Corpus for this space", "duplicate", document_id=existing)
        document_id = _new_id()
        final = DOCUMENTS_DIR / f"{document_id}.{ext}"
        now = db_now()
        row = {
            "document_id": document_id,
            "space_id": space_id,
            "title": _title_from(filename, title),
            "original_filename": filename,
            "file_type": file_type,
            "mime_type": content_type or mimetypes.guess_type(filename)[0],
            "storage_mode": store.STORAGE_COPY,
            "source_path": None,
            "source_mtime": None,
            "stored_path": str(final),
            "sha256": sha,
            "size_bytes": size,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        await store.insert_document(row)
        os.replace(tmp, final)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    saved = await store.get_document(document_id)
    await _broadcast(saved)
    _start(document_id)
    return saved


async def create_from_path(space_id: str, path: str, *, title: str | None = None) -> dict[str, Any]:
    """Create a ``storage_mode='reference'`` document that reads ``path`` in place.

    Nothing is copied: the file is hashed where it is and its path and mtime
    are recorded. Raises ``DocumentError`` for 404/413/415/409.
    """
    src = Path(path).expanduser()
    try:
        src = src.absolute()
    except OSError:
        pass
    if not src.is_file():
        raise DocumentError(404, f"File not found: {path}", "source_missing")
    if not os.access(src, os.R_OK):
        raise DocumentError(403, f"File is not readable: {path}", "source_missing")
    ext, file_type = _validate_extension(src.name)
    st = src.stat()
    cap = max_file_bytes()
    if st.st_size > cap:
        raise DocumentError(413, f"File exceeds the {cap // (1024 * 1024)} MB limit", "too_large")
    if st.st_size == 0:
        raise DocumentError(400, "The file is empty", "parse_error")
    sha = await asyncio.to_thread(_hash_file, src)
    existing = await store.find_by_hash(space_id, sha)
    if existing:
        raise DocumentError(409, "This file is already in the Corpus for this space", "duplicate", document_id=existing)
    document_id = _new_id()
    now = db_now()
    row = {
        "document_id": document_id,
        "space_id": space_id,
        "title": _title_from(src.name, title),
        "original_filename": src.name,
        "file_type": file_type,
        "mime_type": mimetypes.guess_type(src.name)[0],
        "storage_mode": store.STORAGE_REFERENCE,
        "source_path": str(src),
        "source_mtime": float(st.st_mtime),
        "stored_path": None,
        "sha256": sha,
        "size_bytes": int(st.st_size),
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    await store.insert_document(row)
    saved = await store.get_document(document_id)
    await _broadcast(saved)
    _start(document_id)
    return saved


def _start(document_id: str, stages: tuple[str, ...] = ALL_STAGES) -> asyncio.Task:
    """Launch (or relaunch) the ingest task for a document."""
    old = _running.pop(document_id, None)
    if old and not old.done():
        old.cancel()
    task = create_task(run_ingest(document_id, stages), name=f"doc_ingest_{document_id}")
    _running[document_id] = task
    task.add_done_callback(lambda t: _running.pop(document_id, None) if _running.get(document_id) is t else None)
    return task


# ---------------------------------------------------------------------------
# Ingest state machine
# ---------------------------------------------------------------------------


async def run_ingest(document_id: str, stages: tuple[str, ...] = ALL_STAGES) -> None:
    """Run the requested stages for a document.

    ``stages`` is a subset of ``ALL_STAGES``. Embedding, linking and
    summarising can run alone from the stored chunks; parsing and chunking
    always run together and re-read the file.
    """
    row = await store.get_document(document_id)
    if not row:
        return
    was_ready = row["status"] == "ready"
    stages = tuple(s for s in ALL_STAGES if s in stages)
    if "parse" in stages or "chunk" in stages:
        stages = tuple(dict.fromkeys(("parse", "chunk") + stages))
    non_fatal_code: str | None = None
    non_fatal_error: str | None = None
    try:
        units: list[TextUnit] | None = None
        if "parse" in stages:
            try:
                path = store.readable_path(row)
            except store.SourceMissing as e:
                if was_ready:
                    # Keep serving from the stored chunks; only note that the
                    # original is gone so the UI can offer Locate.
                    await store.set_status(document_id, "ready", error=str(e), error_code="source_missing")
                    await _broadcast(await store.get_document(document_id) or row)
                    return
                await _fail(document_id, str(e), "source_missing")
                return
            await _transition(document_id, "parsing")
            parser = for_extension(row["file_type"]) or for_extension(_sanitize_ext(row["original_filename"]))
            if parser is None:
                await _fail(document_id, f"No parser for {row['file_type']}", "unsupported")
                return
            try:
                parsed = await asyncio.to_thread(parser.parse, path)
            except ParseError as e:
                await _fail(document_id, str(e), e.error_code)
                return
            except Exception as e:
                await _fail(document_id, f"Could not parse the file: {e}", "parse_error")
                return
            units = parsed.units

            await _transition(document_id, "chunking")
            try:
                chunks = chunk_units(units)
                if not chunks:
                    await _fail(document_id, "The document contains no text", "no_text")
                    return
                await store.replace_chunks(document_id, chunks)
                fields: dict[str, Any] = {
                    "page_count": parsed.page_count,
                    "chunk_count": len(chunks),
                    "char_count": sum(c.char_count for c in chunks),
                }
                # Prefer a title found inside the file over the filename stem,
                # but never overwrite a title the user chose.
                if parsed.title and row["title"] == _title_from(row["original_filename"], None):
                    fields["title"] = parsed.title[:300]
                await store.update_fields(document_id, **fields)
                row = await store.get_document(document_id) or row
            except Exception as e:
                await _fail(document_id, f"Could not chunk the document: {e}", "parse_error")
                return

        chunk_rows = await store.get_chunks(document_id)
        if not chunk_rows:
            await _fail(document_id, "The document has no indexed text; reprocess it", "no_text")
            return

        # Links are computed before embedding so entity_refs can ride on the
        # chunk metadata; the rows are written in the linking stage.
        links: list[dict[str, Any]] = []
        if "link" in stages:
            try:
                link_units = units or [
                    TextUnit(text=c["text"], page=c["page_start"], heading=c["heading"]) for c in chunk_rows
                ]
                identifiers = extract_identifiers(link_units)
                links = await resolve_entity_links(row["space_id"], link_units, identifiers)
            except Exception as e:
                log.warning("document_link_failed", document_id=document_id, error=str(e))
                links = []
        else:
            links = await store.links_for_document(document_id)
        refs = entity_refs_csv(links)

        if "embed" in stages:
            await _transition(document_id, "embedding")
            try:
                async with _ingest_sem:
                    await _embed_chunks(row, chunk_rows, refs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await _fail(document_id, f"Embedding failed: {e}", "embed_error")
                return
            row = await store.get_document(document_id) or row

        if "link" in stages:
            await _transition(document_id, "linking")
            try:
                await store.replace_links(document_id, links)
            except Exception as e:
                log.warning("document_link_failed", document_id=document_id, error=str(e))

        if "summarize" in stages:
            await _transition(document_id, "summarizing")
            from laya.pipeline.budget import is_budget_paused

            try:
                if await is_budget_paused():
                    non_fatal_code = "summary_skipped_budget"
                    non_fatal_error = "Summary skipped: the monthly budget is paused"
                else:
                    result = await summarize_document(
                        document_id, chunk_rows, row["title"], row["file_type"], row.get("page_count"), row["space_id"],
                    )
                    await store.update_fields(
                        document_id, summary=result["summary"], summary_model=result.get("model"),
                    )
                    row = await store.get_document(document_id) or row
                    if result["summary"]:
                        await embed_document(document_id, result["summary"], _summary_metadata(row, refs))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("document_summary_failed", document_id=document_id, error=str(e))
                non_fatal_code = "summary_error"
                non_fatal_error = f"Summary failed: {e}"
        elif row.get("summary"):
            # Keep the summary vector in step with the current metadata when
            # only the embed stage ran (model switch, space move).
            if "embed" in stages:
                try:
                    await embed_document(document_id, row["summary"], _summary_metadata(row, refs))
                except Exception as e:
                    log.warning("document_summary_reembed_failed", document_id=document_id, error=str(e))

        if non_fatal_code:
            await store.set_status(
                document_id, "ready", error=non_fatal_error, error_code=non_fatal_code, indexed_at=db_now(),
            )
        else:
            await store.set_status(document_id, "ready", clear_error=True, indexed_at=db_now())
        final = await store.get_document(document_id)
        if final:
            await _broadcast(final)
        log.info("document_ready", document_id=document_id, chunks=len(chunk_rows), stages=stages)
    except asyncio.CancelledError:
        current = await _safe_get(document_id)
        if current and current["status"] not in store.TERMINAL_STATUSES:
            try:
                await store.set_status(document_id, "failed", error="Processing was interrupted", error_code="interrupted")
            except Exception:
                pass
        raise
    except Exception as e:
        log.error("document_ingest_failed", document_id=document_id, error=str(e))
        await _fail(document_id, f"Processing failed: {e}", "parse_error")


async def _safe_get(document_id: str) -> dict[str, Any] | None:
    try:
        return await store.get_document(document_id)
    except Exception:
        return None


async def _fail(document_id: str, error: str, error_code: str) -> None:
    try:
        await store.set_status(document_id, "failed", error=error, error_code=error_code)
        row = await store.get_document(document_id)
        if row:
            await _broadcast(row)
    except Exception as e:
        log.warning("document_fail_write_failed", document_id=document_id, error=str(e))
    log.warning("document_failed", document_id=document_id, error_code=error_code, error=error)


def _created_epoch(row: dict[str, Any]) -> float:
    from laya.db.timeutil import DB_TS_FMT
    from datetime import datetime, timezone

    try:
        return datetime.strptime(row["created_at"], DB_TS_FMT).replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time()


def _chunk_metadata(row: dict[str, Any], chunk: dict[str, Any], total: int, refs: str) -> dict[str, Any]:
    return {
        "content_type": "document_chunk",
        "document_id": row["document_id"],
        "parent_id": row["document_id"],
        "chunk_id": chunk["chunk_id"],
        "chunk_index": int(chunk["chunk_index"]),
        "total_chunks": total,
        "page_start": int(chunk["page_start"] or 0),
        "page_end": int(chunk["page_end"] or 0),
        "heading": chunk["heading"] or "",
        "title": row["title"],
        "file_type": row["file_type"],
        "source_platform": "document",
        "space_id": row["space_id"],
        "timestamp": _created_epoch(row),
        "entity_refs": refs,
    }


def _summary_metadata(row: dict[str, Any], refs: str) -> dict[str, Any]:
    return {
        "content_type": "document_summary",
        "document_id": row["document_id"],
        "title": row["title"],
        "file_type": row["file_type"],
        "source_platform": "document",
        "space_id": row["space_id"],
        "timestamp": _created_epoch(row),
        "page_count": int(row.get("page_count") or 0),
        "entity_refs": refs,
    }


async def _embed_chunks(row: dict[str, Any], chunk_rows: list[dict[str, Any]], refs: str) -> None:
    document_id = row["document_id"]
    await delete_where({"document_id": document_id})
    total = len(chunk_rows)

    async def on_batch(done: int, _total: int) -> None:
        await _broadcast({**row, "status": "embedding"}, {"stage": "embedding", "done": done, "total": total})

    await embed_batch(
        [c["chunk_id"] for c in chunk_rows],
        [c["text"] for c in chunk_rows],
        [_chunk_metadata(row, c, total, refs) for c in chunk_rows],
        batch_size=_doc_settings()["embed_batch_size"],
        on_batch=on_batch,
    )
    await store.update_fields(document_id, embedding_model=get_embedding_info().get("backend"))


# ---------------------------------------------------------------------------
# Reprocess, re-sync, relocate
# ---------------------------------------------------------------------------


def stages_for(stage: str) -> tuple[str, ...]:
    """Map the API ``stage`` query value to ingest stages."""
    return {
        "all": ALL_STAGES,
        "embed": ("embed",),
        "summarize": ("summarize",),
        "link": ("link",),
    }[stage]


async def reprocess(document_id: str, stage: str = "all") -> dict[str, Any]:
    row = await store.get_document(document_id)
    if not row:
        raise DocumentError(404, "Document not found", "not_found")
    stages = stages_for(stage)
    if "parse" in stages:
        try:
            store.readable_path(row)
        except store.SourceMissing as e:
            raise DocumentError(409, str(e), "source_missing")
    _start(document_id, stages)
    return row


async def resync(document_id: str) -> dict[str, Any]:
    """Re-ingest a reference document whose file changed on disk.

    Returns ``{"status": "unchanged"}`` when the hash still matches (the
    recorded mtime is refreshed), else ``{"status": "queued"}``.
    """
    row = await store.get_document(document_id)
    if not row:
        raise DocumentError(404, "Document not found", "not_found")
    try:
        path = store.readable_path(row)
    except store.SourceMissing as e:
        raise DocumentError(409, str(e), "source_missing")
    st = path.stat()
    if st.st_size > max_file_bytes():
        raise DocumentError(413, "File exceeds the size limit", "too_large")
    sha = await asyncio.to_thread(_hash_file, path)
    if sha == row["sha256"]:
        await store.update_fields(document_id, source_mtime=float(st.st_mtime))
        row = await store.get_document(document_id) or row
        await _broadcast(row)
        return {"status": "unchanged", "document": row}
    other = await store.find_by_hash(row["space_id"], sha)
    if other and other != document_id:
        raise DocumentError(409, "Another document in this space already has this content", "duplicate", document_id=other)
    await store.update_fields(
        document_id, sha256=sha, size_bytes=int(st.st_size), source_mtime=float(st.st_mtime),
        error=None, error_code=None,
    )
    _start(document_id, ALL_STAGES)
    return {"status": "queued", "document": await store.get_document(document_id)}


async def relocate(document_id: str, path: str) -> dict[str, Any]:
    """Point a reference document at a moved file after verifying its hash."""
    row = await store.get_document(document_id)
    if not row:
        raise DocumentError(404, "Document not found", "not_found")
    if row.get("storage_mode") != store.STORAGE_REFERENCE:
        raise DocumentError(400, "Only documents shared by path can be relocated", "invalid")
    src = Path(path).expanduser().absolute()
    if not src.is_file():
        raise DocumentError(404, f"File not found: {path}", "source_missing")
    sha = await asyncio.to_thread(_hash_file, src)
    if sha != row["sha256"]:
        raise DocumentError(409, "The selected file has different content from the indexed document", "hash_mismatch")
    st = src.stat()
    clear = row.get("error_code") == "source_missing"
    await store.set_status(
        document_id, None, clear_error=clear, source_path=str(src), source_mtime=float(st.st_mtime),
    )
    updated = await store.get_document(document_id)
    await _broadcast(updated)
    return updated


# ---------------------------------------------------------------------------
# Delete, recovery
# ---------------------------------------------------------------------------


async def delete_document_cascade(document_id: str) -> bool:
    """Delete a document's rows, vectors and stored copy. Returns False when
    the id is unknown. A referenced source file is never touched."""
    row = await store.get_document(document_id)
    if not row:
        return False
    task = _running.pop(document_id, None)
    if task and not task.done():
        task.cancel()
    await store.delete_rows(document_id)
    try:
        await asyncio.wait_for(delete_where({"document_id": document_id}), timeout=5.0)
    except Exception as e:
        log.warning("document_vectors_delete_failed", document_id=document_id, error=str(e))
    if row.get("storage_mode") == store.STORAGE_COPY and row.get("stored_path"):
        _unlink_stored_copy(row["stored_path"])
    try:
        await manager.broadcast({
            "type": "document_deleted", "document_id": document_id, "space_id": row.get("space_id"),
        })
    except Exception:
        pass
    log.info("document_deleted", document_id=document_id)
    return True


def _unlink_stored_copy(stored_path: str) -> None:
    # Lexical containment check (normpath + relative_to, not resolve()) so the
    # only filesystem operation on this stored path is the unlink itself.
    base = Path(os.path.normpath(DOCUMENTS_DIR))
    target = Path(os.path.normpath(stored_path))
    try:
        target.relative_to(base)
    except ValueError:
        log.warning("document_copy_outside_dir", path=stored_path)
        return
    try:
        target.unlink(missing_ok=True)
    except OSError as e:
        log.warning("document_copy_unlink_failed", path=stored_path, error=str(e))


async def recover_stalled_documents() -> None:
    """Re-queue documents left mid-ingest by a crash or shutdown."""
    db_rows, _ = await store.list_documents(limit=1000)
    stalled = [r for r in db_rows if r["status"] not in store.TERMINAL_STATUSES]
    interrupted = [r for r in db_rows if r["status"] == "failed" and r.get("error_code") == "interrupted"]
    for r in stalled + interrupted:
        log.info("document_recovered", document_id=r["document_id"], status=r["status"])
        await store.set_status(r["document_id"], "queued", clear_error=True)
        _start(r["document_id"])


def running_count() -> int:
    return sum(1 for t in _running.values() if not t.done())
