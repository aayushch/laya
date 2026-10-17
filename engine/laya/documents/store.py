# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""SQL helpers for Corpus documents (tables from migration 074).

Everything here is plain row access on the shared aiosqlite connection.
Multi-statement writes (chunk and link replacement) run inside
``db/sqlite.transaction()`` so they land atomically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from laya.db.sqlite import get_db, transaction
from laya.db.timeutil import db_now

log = structlog.get_logger()

DOCUMENT_STATUSES = (
    "queued", "parsing", "chunking", "embedding", "linking", "summarizing", "ready", "failed",
)
TERMINAL_STATUSES = {"ready", "failed"}
ERROR_CODES = (
    "no_text", "encrypted", "unsupported", "too_large", "parse_error", "embed_error",
    "summary_error", "summary_skipped_budget", "interrupted", "source_missing",
)
# Error codes a ready document may carry: the stage that failed was optional.
NON_FATAL_ERROR_CODES = {"summary_error", "summary_skipped_budget", "source_missing"}

STORAGE_REFERENCE = "reference"
STORAGE_COPY = "copy"

_DOCUMENT_COLUMNS = (
    "document_id, space_id, title, original_filename, file_type, mime_type, storage_mode, "
    "source_path, source_mtime, stored_path, sha256, size_bytes, page_count, chunk_count, "
    "char_count, status, error, error_code, summary, summary_model, embedding_model, "
    "language, created_at, updated_at, indexed_at"
)


class SourceMissing(Exception):
    """The file a reference-mode document points at cannot be read."""


def canonical_document_ids(document_ids: list[str] | None) -> str | None:
    """Canonical sorted-JSON form of a document-id set, like ``canonical_card_ids``.

    None for an empty set so SQL IS NULL comparisons match conversations with
    no attachments.
    """
    if not document_ids:
        return None
    cleaned = sorted({d for d in document_ids if d})
    if not cleaned:
        return None
    return json.dumps(cleaned, separators=(",", ":"))


def parse_document_ids(raw: str | None) -> list[str]:
    """Inverse of ``canonical_document_ids``; tolerant of bad JSON."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value if v] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _row_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


async def insert_document(row: dict[str, Any]) -> None:
    db = await get_db()
    cols = list(row.keys())
    await db.execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [row[c] for c in cols],
    )
    await db.commit()


async def get_document(document_id: str) -> dict[str, Any] | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE document_id = ?", (document_id,)
    )
    return _row_dict(rows[0]) if rows else None


async def get_documents(document_ids: list[str]) -> list[dict[str, Any]]:
    """Rows for the given ids, in the order requested; unknown ids are skipped."""
    ids = [d for d in dict.fromkeys(document_ids) if d]
    if not ids:
        return []
    db = await get_db()
    placeholders = ",".join("?" * len(ids))
    rows = await db.execute_fetchall(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE document_id IN ({placeholders})", ids
    )
    by_id = {r["document_id"]: _row_dict(r) for r in rows}
    return [by_id[d] for d in ids if d in by_id]


async def find_by_hash(space_id: str, sha256: str) -> str | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT document_id FROM documents WHERE space_id = ? AND sha256 = ?", (space_id, sha256)
    )
    return rows[0]["document_id"] if rows else None


async def list_documents(
    space_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Filtered page of document rows plus the total matching count."""
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if space_id:
        conditions.append("space_id = ?")
        params.append(space_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if q:
        conditions.append("(title LIKE ? OR original_filename LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total_rows = await db.execute_fetchall(f"SELECT COUNT(*) AS c FROM documents {where}", params)
    total = total_rows[0]["c"] if total_rows else 0
    rows = await db.execute_fetchall(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents {where} ORDER BY created_at DESC, document_id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return [_row_dict(r) for r in rows], total


async def set_status(
    document_id: str,
    status: str | None,
    *,
    error: str | None = None,
    error_code: str | None = None,
    clear_error: bool = False,
    **fields: Any,
) -> None:
    """UPDATE a document row: status, error fields and any extra columns.

    ``status=None`` leaves the status alone (used to record a non-fatal error
    code on a document that stays ready). ``clear_error`` resets both error
    columns; otherwise they are written only when given.
    """
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [db_now()]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if clear_error:
        sets.append("error = NULL")
        sets.append("error_code = NULL")
    else:
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        params.append(value)
    params.append(document_id)
    db = await get_db()
    await db.execute(f"UPDATE documents SET {', '.join(sets)} WHERE document_id = ?", params)
    await db.commit()


async def update_fields(document_id: str, **fields: Any) -> None:
    """UPDATE arbitrary columns plus updated_at."""
    await set_status(document_id, None, **fields)


# ---------------------------------------------------------------------------
# Source file state
# ---------------------------------------------------------------------------


def source_state(row: dict[str, Any]) -> str:
    """``copy`` for stored copies; else ``ok``, ``changed`` or ``missing`` for the
    referenced path. One stat per row; the hash is only recomputed on re-sync."""
    if row.get("storage_mode") == STORAGE_COPY:
        return "copy"
    path = row.get("source_path")
    if not path:
        return "missing"
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    recorded = row.get("source_mtime")
    if recorded is not None and abs(float(st.st_mtime) - float(recorded)) > 1e-6:
        return "changed"
    return "ok"


def readable_path(row: dict[str, Any]) -> Path:
    """The file to parse: the stored copy, else the referenced source path.

    Raises ``SourceMissing`` when the file is not a readable regular file.
    """
    raw = row.get("stored_path") if row.get("storage_mode") == STORAGE_COPY else row.get("source_path")
    if not raw:
        raise SourceMissing("The document has no readable file")
    path = Path(raw)
    if not path.is_file():
        raise SourceMissing(f"Source file not found: {path}")
    if not os.access(path, os.R_OK):
        raise SourceMissing(f"Source file is not readable: {path}")
    return path


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


async def replace_chunks(document_id: str, chunks: list[Any]) -> None:
    """Replace a document's chunk rows atomically. Each chunk needs
    ``chunk_index``, ``text``, ``page_start``, ``page_end``, ``heading`` and
    ``char_count`` attributes (``documents/chunking.DocChunk``)."""
    async with transaction() as db:
        await db.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        await db.executemany(
            """INSERT INTO document_chunks
               (chunk_id, document_id, chunk_index, text, page_start, page_end, heading, char_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    chunk_id(document_id, c.chunk_index), document_id, c.chunk_index, c.text,
                    c.page_start, c.page_end, c.heading, c.char_count,
                )
                for c in chunks
            ],
        )


def chunk_id(document_id: str, index: int) -> str:
    return f"{document_id}_chunk_{index}"


async def get_chunks(
    document_id: str,
    *,
    page_from: int | None = None,
    page_to: int | None = None,
    chunk_from: int | None = None,
    chunk_to: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Chunk rows in index order. A page range keeps chunks whose
    ``[page_start, page_end]`` intersects it; a chunk range is inclusive."""
    db = await get_db()
    conditions = ["document_id = ?"]
    params: list[Any] = [document_id]
    if page_from is not None:
        conditions.append("(page_end IS NULL OR page_end >= ?)")
        params.append(page_from)
    if page_to is not None:
        conditions.append("(page_start IS NULL OR page_start <= ?)")
        params.append(page_to)
    if chunk_from is not None:
        conditions.append("chunk_index >= ?")
        params.append(chunk_from)
    if chunk_to is not None:
        conditions.append("chunk_index <= ?")
        params.append(chunk_to)
    sql = (
        "SELECT chunk_id, document_id, chunk_index, text, page_start, page_end, heading, char_count "
        f"FROM document_chunks WHERE {' AND '.join(conditions)} ORDER BY chunk_index"
    )
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = await db.execute_fetchall(sql, params)
    return [_row_dict(r) for r in rows]


async def count_chunks(document_id: str) -> int:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS c FROM document_chunks WHERE document_id = ?", (document_id,)
    )
    return rows[0]["c"] if rows else 0


# ---------------------------------------------------------------------------
# Entity links
# ---------------------------------------------------------------------------


async def replace_links(document_id: str, links: list[dict[str, Any]]) -> None:
    now = db_now()
    async with transaction() as db:
        await db.execute("DELETE FROM document_entity_links WHERE document_id = ?", (document_id,))
        await db.executemany(
            """INSERT OR REPLACE INTO document_entity_links
               (document_id, entity_id, link_method, confidence, mention_count, first_page, matched_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    document_id, link["entity_id"], link.get("link_method", "mention"),
                    float(link.get("confidence", 1.0)), int(link.get("mention_count", 1)),
                    link.get("first_page"), link.get("matched_text"), now,
                )
                for link in links
            ],
        )


async def links_for_document(document_id: str) -> list[dict[str, Any]]:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT l.entity_id, l.link_method, l.confidence, l.mention_count, l.first_page, l.matched_text,
                  (SELECT COUNT(*) FROM action_cards c WHERE c.entity_id = l.entity_id) AS card_count
           FROM document_entity_links l WHERE l.document_id = ?
           ORDER BY l.confidence DESC, l.mention_count DESC""",
        (document_id,),
    )
    return [_row_dict(r) for r in rows]


async def documents_for_entities(
    entity_ids: set[str], space_id: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """entity_id -> ready documents linked to it (with link fields merged in)."""
    ids = [e for e in entity_ids if e]
    if not ids:
        return {}
    db = await get_db()
    placeholders = ",".join("?" * len(ids))
    params: list[Any] = list(ids)
    extra = ""
    if space_id:
        extra = " AND d.space_id = ?"
        params.append(space_id)
    rows = await db.execute_fetchall(
        f"""SELECT d.document_id, d.title, d.file_type, d.page_count, d.space_id, d.status,
                   l.entity_id, l.link_method, l.confidence, l.mention_count, l.first_page, l.matched_text
            FROM document_entity_links l
            JOIN documents d ON d.document_id = l.document_id
            WHERE l.entity_id IN ({placeholders}) AND d.status = 'ready'{extra}
            ORDER BY l.confidence DESC, d.created_at DESC""",
        params,
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["entity_id"], []).append(_row_dict(r))
    return out


async def documents_matching_identifiers(
    identifiers: list[str], space_id: str | None = None
) -> list[dict[str, Any]]:
    """Ready documents whose links were made from one of ``identifiers``."""
    idents = [i for i in dict.fromkeys(identifiers) if i]
    if not idents:
        return []
    db = await get_db()
    conditions = " OR ".join("l.matched_text LIKE ?" for _ in idents)
    params: list[Any] = [f"%{i}%" for i in idents]
    extra = ""
    if space_id:
        extra = " AND d.space_id = ?"
        params.append(space_id)
    rows = await db.execute_fetchall(
        f"""SELECT DISTINCT d.document_id, d.title, d.file_type, d.page_count, d.space_id,
                   l.entity_id, l.link_method, l.confidence, l.first_page
            FROM document_entity_links l
            JOIN documents d ON d.document_id = l.document_id
            WHERE ({conditions}) AND d.status = 'ready'{extra}
            ORDER BY l.confidence DESC""",
        params,
    )
    return [_row_dict(r) for r in rows]


async def space_has_ready_documents(space_id: str | None) -> bool:
    db = await get_db()
    if space_id:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM documents WHERE status = 'ready' AND space_id = ? LIMIT 1", (space_id,)
        )
    else:
        rows = await db.execute_fetchall("SELECT 1 FROM documents WHERE status = 'ready' LIMIT 1")
    return bool(rows)


async def delete_rows(document_id: str) -> None:
    """Delete links, chunks (row by row, so FTS triggers fire) and the document."""
    async with transaction() as db:
        await db.execute("DELETE FROM document_entity_links WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))


# ---------------------------------------------------------------------------
# API shape
# ---------------------------------------------------------------------------


def to_document_response(
    row: dict[str, Any],
    *,
    current_backend: str | None,
    entity_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the ``DocumentResponse`` dict (models/document.py) from a row."""
    embedding_model = row.get("embedding_model")
    return {
        "document_id": row["document_id"],
        "space_id": row["space_id"],
        "title": row["title"],
        "original_filename": row["original_filename"],
        "file_type": row["file_type"],
        "mime_type": row.get("mime_type"),
        "size_bytes": row["size_bytes"],
        "page_count": row.get("page_count"),
        "chunk_count": row.get("chunk_count") or 0,
        "char_count": row.get("char_count") or 0,
        "storage_mode": row.get("storage_mode") or STORAGE_REFERENCE,
        "source_path": row.get("source_path"),
        "source_state": source_state(row),
        "status": row["status"],
        "error": row.get("error"),
        "error_code": row.get("error_code"),
        "summary": row.get("summary"),
        "summary_model": row.get("summary_model"),
        "embedding_model": embedding_model,
        "needs_reembed": bool(
            row.get("status") == "ready" and embedding_model and current_backend
            and embedding_model != current_backend
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "indexed_at": row.get("indexed_at"),
        "entity_links": [
            {
                "entity_id": link["entity_id"],
                "link_method": link["link_method"],
                "confidence": link["confidence"],
                "mention_count": link.get("mention_count") or 1,
                "first_page": link.get("first_page"),
                "matched_text": link.get("matched_text"),
                "card_count": link.get("card_count") or 0,
            }
            for link in (entity_links or [])
        ],
    }
