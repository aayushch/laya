# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Semantic search tool implementation."""

from __future__ import annotations

from typing import Any

from laya.db.chromadb_store import memory_search
from laya.llm.tools.constants import (
    SEMANTIC_SEARCH_DEFAULT,
    SEMANTIC_SEARCH_MAX,
    parse_iso_to_timestamp,
)


async def semantic_search(
    query: str,
    n_results: int = SEMANTIC_SEARCH_DEFAULT,
    space_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Perform semantic search across all stored content."""
    filters: list[dict[str, Any]] = []
    if space_id:
        filters.append({"space_id": space_id})

    date_from_ts = parse_iso_to_timestamp(date_from)
    date_to_ts = parse_iso_to_timestamp(date_to, end_of_day=True)
    if date_from_ts is not None:
        filters.append({"timestamp": {"$gte": date_from_ts}})
    if date_to_ts is not None:
        filters.append({"timestamp": {"$lte": date_to_ts}})

    where: dict[str, Any] | None = None
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    results = await memory_search(query, n_results=min(n_results, SEMANTIC_SEARCH_MAX), where=where)

    return {
        "results": [_format_hit(r) for r in results],
        "count": len(results),
    }


def _format_hit(r: dict[str, Any]) -> dict[str, Any]:
    """One hit; Corpus document vectors also carry their document fields so the
    model can cite them as [doc:ID p.N]."""
    meta = r["metadata"]
    hit: dict[str, Any] = {
        "id": r["id"],
        "content": r["document"][:500],
        "content_type": meta.get("content_type", "unknown"),
        "source_platform": meta.get("source_platform", ""),
        "distance": r.get("distance"),
    }
    if meta.get("document_id"):
        hit["document_id"] = meta["document_id"]
        hit["title"] = meta.get("title", "")
        if meta.get("page_start"):
            hit["page_start"] = meta.get("page_start")
            hit["page_end"] = meta.get("page_end")
    return hit
