# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Semantic search tool implementation."""

from __future__ import annotations

from typing import Any

from laya.db.chromadb_store import memory_search
from laya.llm.tools.constants import SEMANTIC_SEARCH_DEFAULT, SEMANTIC_SEARCH_MAX


async def semantic_search(
    query: str,
    n_results: int = SEMANTIC_SEARCH_DEFAULT,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Perform semantic search across all stored content."""
    where = {"space_id": space_id} if space_id else None
    results = await memory_search(query, n_results=min(n_results, SEMANTIC_SEARCH_MAX), where=where)

    return {
        "results": [
            {
                "id": r["id"],
                "content": r["document"][:500],
                "content_type": r["metadata"].get("content_type", "unknown"),
                "source_platform": r["metadata"].get("source_platform", ""),
                "distance": r.get("distance"),
            }
            for r in results
        ],
        "count": len(results),
    }
