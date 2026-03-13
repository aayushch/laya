"""Semantic search tool implementation."""

from __future__ import annotations

from typing import Any

from laya.db.chromadb_store import memory_search


async def semantic_search(
    query: str,
    n_results: int = 10,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Perform semantic search across all stored content."""
    where = {"space_id": space_id} if space_id else None
    results = await memory_search(query, n_results=min(n_results, 20), where=where)

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
