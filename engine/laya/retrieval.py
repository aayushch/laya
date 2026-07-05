# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared hybrid-retrieval primitives.

The chat, trace, and card-search retrieval paths each independently
reimplemented the same building blocks — an English stopword set, keyword
extraction, and Reciprocal Rank Fusion — which then drifted (the two RRF copies
resolved doc ids differently; the stopword sets were copy-pasted). This module is
the single home for them so the three stacks can't diverge (review §5.3 — P7-1).

FTS5 query building (``build_fts_match``) and the ``fts_ready`` flag stay in
``laya.db.fts``, which is deliberately import-free so the low-level DB layer has
no dependency back on the pipeline.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from laya.db.fts import build_fts_match, fts_ready

log = structlog.get_logger()

# Shared English stopword set. Was duplicated verbatim in pipeline/chat.py and
# pipeline/trace.py; db/fts.py keeps its own small copy on purpose to stay
# import-free, so it is intentionally NOT consolidated here.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "am", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "it", "they", "them",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "how", "when", "where", "why", "and", "or", "but", "not", "no",
    "if", "then", "so", "to", "of", "in", "on", "at", "by", "for",
    "with", "about", "from", "up", "out", "into", "over", "after",
    "all", "any", "some", "just", "also", "than", "very", "too",
})


def extract_keywords(
    query: str, *, min_len: int = 3, max_terms: int | None = None
) -> list[str]:
    """Split a query into keywords, dropping stopwords and terms shorter than
    ``min_len``. Original case is preserved (only the stopword test lowercases).

    Callers pick ``min_len`` by intent: chat retrieval uses 3 (precise), trace
    uses 2 (broader recall). ``max_terms`` optionally caps the result.
    """
    kws = [w for w in query.split() if len(w) >= min_len and w.lower() not in STOPWORDS]
    return kws[:max_terms] if max_terms is not None else kws


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]], k: int = 60
) -> list[dict]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion.

    ``score(d) = Σ 1/(k + rank_i(d))`` across every list that contains ``d``; a
    larger ``k`` damps the influence of any single list's top ranks.

    The doc id resolves across the id fields the different stacks use — ``id``
    (semantic hits), ``card_id`` (cards), ``event_id`` (events), ``entity_id``
    (entity correlations) — so cards/events/entities dedupe consistently. The two
    former copies checked either event_id OR entity_id but not both.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            doc_id = (
                item.get("id")
                or item.get("card_id")
                or item.get("event_id")
                or item.get("entity_id")
                or str(rank)
            )
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in items:
                items[doc_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
    return [items[did] for did in sorted_ids]


async def fts_or_like(
    query: str | None,
    *,
    min_len: int,
    max_terms: int,
    match_all: bool = False,
    fts: Callable[[str], Awaitable[Any]],
    like: Callable[[str | None], Awaitable[Any]],
    warn_event: str,
) -> Any:
    """Run the shared "FTS5/BM25 when available, else SQL LIKE" keyword dispatch.

    Builds a safe MATCH expression from ``query``; if FTS is ready and the query
    yields usable terms, runs ``fts(match)`` inside a try/except that logs
    ``warn_event`` and degrades to ``like(query)`` on any runtime FTS fault (an
    exotic SQLite build without fts5, a transient index error). When no terms
    survive (stopwords-only / too short) or FTS is unavailable, runs
    ``like(query)`` directly.

    The FTS body, the LIKE body, and their result shapes stay in each caller —
    only this try-FTS-then-fall-back control flow is shared. It was copy-pasted
    across chat, trace, and the card-search tool and had begun to drift (e.g. the
    warn-event labels and the redundant ``query and`` guard differed), so it lives
    here now so the fallback contract can't diverge (review §5.3 — P7-1).

    ``min_len`` / ``max_terms`` / ``match_all`` are passed through to
    ``build_fts_match`` unchanged — each stack keeps its own tuned values (chat
    min_len 3 for precision, trace 2 for recall, the card tool ``match_all`` to
    mirror its LIKE fallback's AND semantics).
    """
    match = (
        build_fts_match(query, min_len=min_len, max_terms=max_terms, match_all=match_all)
        if query
        else None
    )
    if fts_ready() and match:
        try:
            return await fts(match)
        except Exception as e:
            log.warning(warn_event, error=str(e))
    return await like(query)
