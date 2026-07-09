# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared per-event related-context retrieval (review §3/§4 — P6-7).

The router, the stager, and each persona/engineer worker independently embedded
and searched ChromaDB with the *same* ``"{title} {body[:300]}"`` query — 2-4
identical embeddings per event on the hot pipeline path (the embedding is the
expensive part, especially on CPU/MPS). This computes the embedding+search ONCE
per event (top-5) and memoizes it, so every stage reuses the same result and just
slices to the breadth it wants. All stages already query the same past content
(the event's own card isn't indexed until emit), so a single snapshot is also
more consistent than 2-4 separately-timed searches.
"""

from __future__ import annotations

from collections import OrderedDict

import structlog

from laya.db.chromadb_store import memory_search
from laya.models.event import LayaEvent

log = structlog.get_logger()

# Widest breadth any stage asks for (engineer/ops/finance use 5); callers slice
# down. Computing at the max lets every caller share one embedding+search.
_MAX_N = 5

# Per-event LRU cache. Bounded so a long-running process can't grow it without
# limit; an event evicted before a late retry simply recomputes. Cleared per-test
# via the `db` fixture (see conftest) for isolation.
_cache: "OrderedDict[str, list[dict]]" = OrderedDict()
_CACHE_MAX = 128


def clear_related_context_cache() -> None:
    """Drop all memoized event contexts (used by tests for isolation)."""
    _cache.clear()


async def query_related_context(event: LayaEvent, n_results: int = 3) -> list[dict]:
    """Return up to ``n_results`` past items related to ``event``, computing the
    embedding+ChromaDB search at most once per event (memoized). Never raises —
    a search failure yields an empty list, as each call site did before."""
    key = event.event_id
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached[:n_results]

    query = f"{event.subject.title} {event.content.body[:300]}"
    try:
        results = await memory_search(query, n_results=_MAX_N)
    except Exception as e:
        log.warning("related_context_search_skipped", error=str(e), event_id=key)
        results = []

    _cache[key] = results
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return results[:n_results]
