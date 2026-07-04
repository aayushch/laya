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
