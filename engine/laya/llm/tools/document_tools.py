# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Corpus document tools for chat and MCP: list, search, read."""

from __future__ import annotations

from typing import Any

from laya.documents import search as document_search
from laya.documents import store
from laya.llm.tools.constants import (
    DOCUMENT_LIST_MAX,
    DOCUMENT_READ_MAX_CHARS,
    DOCUMENT_SEARCH_DEFAULT,
    DOCUMENT_SEARCH_MAX,
)

_DEFAULT_READ_CHUNKS = 8
_SEARCH_TEXT_CHARS = 800
_SUMMARY_PREVIEW_CHARS = 300


async def list_documents(
    space_id: str | None = None,
    status: str | None = "ready",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List Corpus documents (ready ones by default)."""
    limit = max(1, min(int(limit or 50), DOCUMENT_LIST_MAX))
    rows, total = await store.list_documents(
        space_id=space_id, status=status or None, limit=limit, offset=max(0, int(offset or 0)),
    )
    docs = []
    for r in rows:
        summary = (r.get("summary") or "").strip()
        docs.append({
            "document_id": r["document_id"],
            "title": r["title"],
            "file_type": r["file_type"],
            "page_count": r.get("page_count"),
            "chunk_count": r.get("chunk_count") or 0,
            "status": r["status"],
            "space_id": r["space_id"],
            "created_at": r["created_at"],
            "summary_preview": summary[:_SUMMARY_PREVIEW_CHARS] + ("..." if len(summary) > _SUMMARY_PREVIEW_CHARS else ""),
        })
    return {
        "documents": docs,
        "total": total,
        "has_more": offset + len(rows) < total,
        "hint": "Cite as [doc:DOCUMENT_ID] or [doc:DOCUMENT_ID p.N].",
    }


async def search_documents(
    query: str,
    document_id: str | None = None,
    limit: int = DOCUMENT_SEARCH_DEFAULT,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Hybrid (vector + keyword) search over document chunks."""
    if not (query or "").strip():
        return {"results": [], "count": 0, "error": "query is required"}
    limit = max(1, min(int(limit or DOCUMENT_SEARCH_DEFAULT), DOCUMENT_SEARCH_MAX))
    hits = await document_search.hybrid_chunk_search(
        query.strip(), space_id, n=limit, document_ids=[document_id] if document_id else None,
    )
    results = []
    for h in hits:
        meta = h.get("metadata", {})
        results.append({
            "document_id": h["document_id"],
            "title": meta.get("title", ""),
            "chunk_id": h.get("chunk_id"),
            "chunk_index": h.get("chunk_index"),
            "page_start": meta.get("page_start"),
            "page_end": meta.get("page_end"),
            "heading": meta.get("heading"),
            "is_summary": bool(meta.get("is_summary")),
            "text": (h.get("text") or "")[:_SEARCH_TEXT_CHARS],
        })
    return {"results": results, "count": len(results), "hint": "Cite as [doc:DOCUMENT_ID p.N]."}


async def read_document(
    document_id: str,
    page_from: int | None = None,
    page_to: int | None = None,
    chunk_from: int | None = None,
    chunk_to: int | None = None,
    max_chars: int = 10000,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Read a document's text by page range or chunk range (first chunks by default)."""
    row = await store.get_document(document_id)
    if not row:
        return {"error": f"Document not found: {document_id}"}
    if space_id and row["space_id"] != space_id:
        return {"error": f"Document {document_id} belongs to another space"}
    max_chars = max(500, min(int(max_chars or 10000), DOCUMENT_READ_MAX_CHARS))
    ranged = any(v is not None for v in (page_from, page_to, chunk_from, chunk_to))
    if page_from is not None or page_to is not None:
        chunks = await store.get_chunks(document_id, page_from=page_from, page_to=page_to)
    elif chunk_from is not None or chunk_to is not None:
        chunks = await store.get_chunks(document_id, chunk_from=chunk_from, chunk_to=chunk_to)
    else:
        chunks = await store.get_chunks(document_id, limit=_DEFAULT_READ_CHUNKS)
    total_chunks = await store.count_chunks(document_id)

    out: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for c in chunks:
        if used + len(c["text"]) > max_chars and out:
            truncated = True
            break
        out.append({
            "chunk_index": c["chunk_index"],
            "page_start": c["page_start"],
            "page_end": c["page_end"],
            "heading": c["heading"],
            "text": c["text"][:max_chars],
        })
        used += len(c["text"])
    last_index = out[-1]["chunk_index"] if out else -1
    has_more = truncated or (last_index + 1 < total_chunks and not ranged) or (ranged and truncated)
    result: dict[str, Any] = {
        "document_id": document_id,
        "title": row["title"],
        "file_type": row["file_type"],
        "page_count": row.get("page_count"),
        "total_chunks": total_chunks,
        "chunks": out,
        "has_more": has_more,
        "next_chunk_index": last_index + 1 if last_index + 1 < total_chunks else None,
        "hint": "Cite as [doc:DOCUMENT_ID p.N]. Use chunk_from/chunk_to or page_from/page_to to read more.",
    }
    if not ranged:
        result["summary"] = row.get("summary")
    return result
