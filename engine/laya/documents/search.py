# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Hybrid search over document chunks, shared by chat retrieval and the
``search_documents`` tool: vector hits from Chroma and BM25 hits from
``documents_fts`` (LIKE when FTS5 is unavailable), fused with RRF.

Every result is ``{id, document_id, chunk_id, type: "document", text, metadata, source}``
where ``id`` is the chunk id (the Chroma id and the SQLite key are the same
string, so the two signals fuse on it). A document-level summary hit has
``id == document_id``, ``chunk_id None`` and ``metadata.is_summary True``.
"""

from __future__ import annotations

from typing import Any

import structlog

from laya.db.chromadb_store import DOCUMENT_CONTENT_TYPES, and_where, memory_search
from laya.db.sqlite import get_db
from laya.retrieval import extract_keywords, fts_or_like, reciprocal_rank_fusion

log = structlog.get_logger()

DOCUMENT_SEMANTIC_MAX_DISTANCE = 0.60


def _ids_where(document_ids: list[str] | None) -> dict[str, Any] | None:
    if not document_ids:
        return None
    if len(document_ids) == 1:
        return {"document_id": document_ids[0]}
    return {"document_id": {"$in": list(document_ids)}}


async def vector_chunk_search(
    query: str,
    space_id: str | None,
    n: int = 10,
    document_ids: list[str] | None = None,
    max_distance: float | None = DOCUMENT_SEMANTIC_MAX_DISTANCE,
) -> list[dict[str, Any]]:
    """Chroma search restricted to document vectors (chunks and summaries)."""
    where = and_where(
        {"content_type": {"$in": list(DOCUMENT_CONTENT_TYPES)}},
        {"space_id": space_id} if space_id else None,
        _ids_where(document_ids),
    )
    results = await memory_search(query, n_results=n, where=where, max_distance=max_distance)
    out: list[dict[str, Any]] = []
    for r in results:
        meta = r.get("metadata") or {}
        is_summary = meta.get("content_type") == "document_summary"
        doc_id = meta.get("document_id") or r["id"]
        out.append({
            "id": r["id"],
            "document_id": doc_id,
            "chunk_id": None if is_summary else r["id"],
            "chunk_index": meta.get("chunk_index"),
            "type": "document",
            "text": r.get("document") or "",
            "metadata": {
                "title": meta.get("title", ""),
                "page_start": meta.get("page_start") or None,
                "page_end": meta.get("page_end") or None,
                "heading": meta.get("heading") or None,
                "file_type": meta.get("file_type", ""),
                "space_id": meta.get("space_id"),
                "is_summary": is_summary,
            },
            "source": "doc_semantic",
            "distance": r.get("distance"),
        })
    return out


async def keyword_chunk_search(
    query: str,
    space_id: str | None,
    n: int = 10,
    document_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """BM25 over ``documents_fts`` (title x3, heading x2, text x1), else LIKE."""
    return await fts_or_like(
        query,
        min_len=3,
        max_terms=8,
        fts=lambda m: _keyword_fts(m, space_id, n, document_ids),
        like=lambda q: _keyword_like(q, space_id, n, document_ids),
        warn_event="document_fts_failed_fallback_like",
    )


_SELECT = (
    "SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.page_start, c.page_end, c.heading, "
    "d.title, d.file_type, d.space_id"
)


def _scope(space_id: str | None, document_ids: list[str] | None) -> tuple[str, list[Any]]:
    sql = " AND d.status = 'ready'"
    params: list[Any] = []
    if space_id:
        sql += " AND d.space_id = ?"
        params.append(space_id)
    if document_ids:
        sql += f" AND d.document_id IN ({','.join('?' * len(document_ids))})"
        params.extend(document_ids)
    return sql, params


async def _keyword_fts(match: str, space_id: str | None, n: int, document_ids: list[str] | None) -> list[dict[str, Any]]:
    db = await get_db()
    scope_sql, scope_params = _scope(space_id, document_ids)
    rows = await db.execute_fetchall(
        f"""{_SELECT}
            FROM documents_fts
            JOIN document_chunks c ON c.chunk_id = documents_fts.chunk_id
            JOIN documents d ON d.document_id = c.document_id
            WHERE documents_fts MATCH ?{scope_sql}
            ORDER BY bm25(documents_fts, 0, 0, 3.0, 2.0, 1.0) LIMIT ?""",
        [match, *scope_params, n],
    )
    return _format_rows(rows)


async def _keyword_like(query: str | None, space_id: str | None, n: int, document_ids: list[str] | None) -> list[dict[str, Any]]:
    keywords = extract_keywords(query or "", min_len=3)
    if not keywords:
        return []
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []
    for kw in keywords[:8]:
        conditions.append("(c.text LIKE ? OR c.heading LIKE ? OR d.title LIKE ?)")
        params.extend([f"%{kw}%"] * 3)
    scope_sql, scope_params = _scope(space_id, document_ids)
    rows = await db.execute_fetchall(
        f"""{_SELECT}
            FROM document_chunks c
            JOIN documents d ON d.document_id = c.document_id
            WHERE ({' OR '.join(conditions)}){scope_sql}
            ORDER BY d.created_at DESC, c.chunk_index ASC LIMIT ?""",
        [*params, *scope_params, n],
    )
    return _format_rows(rows)


def _format_rows(rows) -> list[dict[str, Any]]:
    return [
        {
            "id": r["chunk_id"],
            "document_id": r["document_id"],
            "chunk_id": r["chunk_id"],
            "chunk_index": r["chunk_index"],
            "type": "document",
            "text": r["text"],
            "metadata": {
                "title": r["title"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "heading": r["heading"],
                "file_type": r["file_type"],
                "space_id": r["space_id"],
                "is_summary": False,
            },
            "source": "doc_keyword",
        }
        for r in rows
    ]


async def hybrid_chunk_search(
    query: str,
    space_id: str | None,
    n: int = 10,
    document_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Vector and keyword chunk search fused with RRF, top ``n``."""
    import asyncio

    vec, kw = await asyncio.gather(
        vector_chunk_search(query, space_id, n=n, document_ids=document_ids),
        keyword_chunk_search(query, space_id, n=n, document_ids=document_ids),
        return_exceptions=True,
    )
    lists: list[list[dict[str, Any]]] = []
    for r in (vec, kw):
        if isinstance(r, list):
            lists.append(r)
        elif isinstance(r, Exception):
            log.warning("document_search_signal_failed", error=str(r))
    fused = reciprocal_rank_fusion(lists, k=60)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in fused:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
        if len(out) >= n:
            break
    return out
