# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Trace pipeline — semantic cross-platform entity search.

Three-phase search:
  1. Discovery  — ChromaDB semantic + SQLite fuzzy + entity lookup, merged via RRF
  2. Expansion  — fetch ALL cards for matched entities + cross-references
  3. Clustering — group by connected entities, order chronologically, detect chapters
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone

import structlog

from laya.api.cards_api import CARD_SELECT_COLUMNS, _row_to_card
from laya.pipeline.queue import _get_semaphore
from laya.api.websocket import manager
from laya.db.chromadb_store import memory_search
from laya.retrieval import extract_keywords, fts_or_like, reciprocal_rank_fusion
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.config import get_self_user
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call, llm_call_streaming
from laya.llm.tools.constants import (
    TRACE_ENTITY_SEARCH_MAX,
    TRACE_EVENT_SEARCH_MAX,
    TRACE_FUZZY_SEARCH_MAX,
    TRACE_IDENTIFIER_SEARCH_MAX,
    TRACE_SEMANTIC_SEARCH_MAX,
    TRACE_TEXT_SEARCH_MAX,
)
from laya.llm.prompts.trace import build_narrative_messages, build_summary_messages
from laya.llm.prompts.trace_filter import (
    RELEVANCE_FILTER_SCHEMA,
    build_relevance_filter_messages,
)
from laya.models.card import CardResponse
from laya.models.trace import (
    SearchMetadata,
    TraceChapter,
    TraceCluster,
    TraceEntity,
    TraceRequest,
    TraceResponse,
    TraceStatusSummary,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Cancellation support — per-trace asyncio.Event signals
# ---------------------------------------------------------------------------
_cancel_events: dict[str, asyncio.Event] = {}


class TraceCancelled(Exception):
    """Raised when a trace is cancelled mid-execution."""


class TraceAlreadyRunning(Exception):
    """Raised when a trace_id is already executing (concurrent rerun of the same id)."""


def request_cancel(trace_id: str) -> bool:
    """Signal a running trace to abort. Returns True if there was a trace to cancel."""
    ev = _cancel_events.get(trace_id)
    if ev:
        ev.set()
        return True
    return False


def _check_cancelled(trace_id: str) -> None:
    """Raise TraceCancelled if the trace has been cancelled."""
    ev = _cancel_events.get(trace_id)
    if ev and ev.is_set():
        raise TraceCancelled(f"Trace {trace_id} cancelled")


async def _cancellable(coro, trace_id: str):
    """Run a coroutine but abort immediately if the trace is cancelled.

    Wraps the coroutine in a task and races it against the cancel event.
    If cancelled, the underlying task is cancelled too (aborting the HTTP
    request to the LLM provider).
    """
    ev = _cancel_events.get(trace_id)
    if not ev:
        return await coro

    task = asyncio.ensure_future(coro)
    cancel_waiter = asyncio.ensure_future(ev.wait())

    done, pending = await asyncio.wait(
        {task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED
    )

    for p in pending:
        p.cancel()
        try:
            await p
        except (asyncio.CancelledError, Exception):
            pass

    if cancel_waiter in done:
        raise TraceCancelled(f"Trace {trace_id} cancelled")

    return task.result()

# Regex to detect identifier patterns like "PR 540", "PR-540", "PR#540",
# LAYA"-986", "BUG-123", "ISSUE 42", etc.
_IDENTIFIER_RE = re.compile(
    r"([A-Za-z]{1,10})[\s\-#]?(\d{1,6})",
)

# ---------------------------------------------------------------------------
# Pre-retrieval feedback & post-retrieval relevance filter
# ---------------------------------------------------------------------------


async def _query_trace_feedback(query: str) -> dict:
    """Query past cluster-removal feedback to exclude/demote entities.

    Returns:
        {"exact_exclude": set[str], "global_demote": set[str]}
    """
    db = await get_db()
    exact_exclude: set[str] = set()
    global_demote: set[str] = set()

    # Exact: entities net-removed for this specific query
    rows = await db.execute_fetchall(
        """SELECT entity_id
           FROM trace_feedback WHERE query = ?
           GROUP BY entity_id
           HAVING SUM(CASE WHEN action = 'removed' THEN 1 ELSE -1 END) > 0""",
        (query,),
    )
    for row in rows:
        exact_exclude.add(row["entity_id"])

    # Global: entities removed across 3+ different queries
    rows = await db.execute_fetchall(
        """SELECT entity_id
           FROM trace_feedback WHERE action = 'removed'
           GROUP BY entity_id
           HAVING COUNT(DISTINCT query) >= 3""",
    )
    for row in rows:
        global_demote.add(row["entity_id"])

    return {"exact_exclude": exact_exclude, "global_demote": global_demote}


async def _llm_relevance_filter(
    query: str,
    seeds: list[dict],
    all_cards: list[CardResponse],
    trace_id: str | None = None,
    space_id: str | None = None,
) -> tuple[list[dict], int]:
    """Batch LLM call to judge whether each non-identifier seed is relevant.

    Returns (filtered_seeds, count_removed). Fails open on error.
    """
    # Identifier matches are always kept — they're exact and reliable
    id_seeds = [s for s in seeds if s.get("source") == "identifier"]
    other_seeds = [s for s in seeds if s.get("source") != "identifier"]

    if not other_seeds:
        return seeds, 0

    # Build card lookup for quick access
    card_by_id: dict[str, CardResponse] = {}
    for c in all_cards:
        card_by_id[c.card_id] = c

    # Build candidates with content for LLM
    candidates: list[dict] = []
    seed_index_map: dict[int, dict] = {}  # seed_index -> seed dict
    for i, seed in enumerate(other_seeds):
        card_id = seed.get("card_id") or seed.get("id")
        card = card_by_id.get(card_id) if card_id else None
        # If no card found by card_id, try matching via entity_id
        if not card and seed.get("entity_id"):
            for c in all_cards:
                if c.entity_id == seed["entity_id"]:
                    card = c
                    break
        candidates.append({
            "seed_index": i,
            "header": card.header if card else seed.get("id", "unknown"),
            "summary": card.summary if card else "",
        })
        seed_index_map[i] = seed

    try:
        messages = build_relevance_filter_messages(query, candidates)
        llm_coro = llm_call(
            role="router",
            messages=messages,
            response_schema=RELEVANCE_FILTER_SCHEMA,
            step="trace_filter",
            temperature=0.0,
            max_tokens=DEFAULT_MAX_TOKENS,
            space_id=space_id,
        )
        # Race the LLM call against the cancel event so abort is near-instant
        response = await (_cancellable(llm_coro, trace_id) if trace_id else llm_coro)

        if not response.parsed or "judgments" not in response.parsed:
            log.warning("trace_filter_no_judgments", trace_id=trace_id)
            return seeds, 0

        # Collect relevant seed indices
        relevant_indices: set[int] = set()
        for j in response.parsed["judgments"]:
            if j.get("relevant"):
                relevant_indices.add(j["seed_index"])
            else:
                log.debug(
                    "trace_filter_removed",
                    seed_index=j["seed_index"],
                    reason=j.get("reason", ""),
                    header=candidates[j["seed_index"]]["header"][:60]
                    if j["seed_index"] < len(candidates) else "?",
                )

        # Build filtered list: all identifier seeds + relevant non-identifier seeds
        filtered = list(id_seeds)
        for i, seed in enumerate(other_seeds):
            if i in relevant_indices:
                filtered.append(seed)

        removed = len(seeds) - len(filtered)
        log.info("trace_filter_complete", trace_id=trace_id, kept=len(filtered), removed=removed)
        return filtered, removed

    except Exception as e:
        log.warning("trace_filter_failed", trace_id=trace_id, error=str(e))
        return seeds, 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_trace(request: TraceRequest, trace_id: str | None = None) -> TraceResponse:
    """Execute a full trace: discovery → expansion → clustering.

    `trace_id` is supplied by a rerun so the run REUSES the existing identity and
    updates that row in place (see _save_trace's upsert). Minting a fresh id on
    rerun — and deleting the old row up front, as the API used to — meant every
    history link and in-flight client poll 404'd the instant a rerun started, and
    a cancel/crash mid-run destroyed the trace permanently. Reusing the id and
    upserting only on success makes rerun idempotent and crash-safe.
    """
    t0 = time.monotonic()
    trace_id = trace_id or f"trace_{uuid.uuid4().hex[:12]}"

    # _cancel_events is keyed by trace_id. With unique-per-run ids a same-id
    # collision was impossible; now that reruns reuse an id, two concurrent runs
    # of the SAME id would clobber each other's cancel event (making the first
    # uncancellable, and letting the first's finally-pop deregister the second)
    # and would race the same row in _save_trace. Reject the second run instead.
    if trace_id in _cancel_events:
        raise TraceAlreadyRunning(trace_id)

    # Register cancellation event for this trace
    cancel_event = asyncio.Event()
    _cancel_events[trace_id] = cancel_event

    # Logged here (not in the API handlers) so create + rerun both carry a trace_id
    # from the first line — the API's trace_requested fires before the id is minted,
    # which made lifecycle events impossible to correlate across a run.
    log.info("trace_started", trace_id=trace_id, query=request.query, space_id=request.space_id)

    async def _progress(stage: str, step: int, total: int) -> None:
        _check_cancelled(trace_id)  # Check before each stage
        await manager.broadcast({
            "type": "trace_progress",
            "trace_id": trace_id,
            "query": request.query,
            "stage": stage,
            "step": step,
            "total": total,
        })

    try:
        return await _run_trace_inner(request, trace_id, _progress, t0)
    except TraceCancelled:
        log.info("trace_cancelled", trace_id=trace_id)
        await manager.broadcast({
            "type": "trace_cancelled",
            "trace_id": trace_id,
        })
        raise
    finally:
        _cancel_events.pop(trace_id, None)


async def _run_trace_inner(
    request: TraceRequest,
    trace_id: str,
    _progress,
    t0: float,
) -> TraceResponse:
    """Inner trace execution — separated so run_trace can handle cancellation."""
    # Phase 1 — Discovery
    # Build search signals based on request flags. All default to enabled
    # for backward compat. Advanced settings let users disable stages.
    total_steps = 6
    await _progress("Searching", 1, total_steps)
    coros: list = []
    signal_labels: list[str] = []

    if request.enable_identifier:
        coros.append(_identifier_search(request.query, request.space_id, n=TRACE_IDENTIFIER_SEARCH_MAX))
        signal_labels.append("identifier")
    if request.enable_semantic:
        coros.append(_semantic_search(request.query, request.space_id, n=TRACE_SEMANTIC_SEARCH_MAX))
        signal_labels.append("semantic")
    if request.enable_entity:
        coros.append(_entity_table_search(request.query, n=TRACE_ENTITY_SEARCH_MAX))
        signal_labels.append("entity")

    # Text search: strict phrase-match LIKE on card content only.
    if request.enable_text:
        coros.append(_card_text_search(
            request.query, request.space_id, n=TRACE_TEXT_SEARCH_MAX,
            include_archived=request.include_archived,
        ))
        signal_labels.append("text")

    # Fuzzy search: keyword-split LIKE on cards + events — broader but noisier.
    if request.fuzzy_search:
        coros.append(_card_fuzzy_search(
            request.query, request.space_id, n=TRACE_FUZZY_SEARCH_MAX,
            include_archived=request.include_archived,
        ))
        coros.append(_event_keyword_search(request.query, request.space_id, n=TRACE_EVENT_SEARCH_MAX))
        signal_labels.extend(["fuzzy", "event"])

    results = await _cancellable(
        asyncio.gather(*coros, return_exceptions=True), trace_id
    )

    # Collect successful results.
    # Identifier matches are guaranteed seeds — they bypass RRF to ensure
    # precise matches (like "PR-540") aren't drowned by broader signals.
    guaranteed_seeds: list[dict] = []
    ranked_lists: list[list[dict]] = []
    meta = SearchMetadata(
        fuzzy_search=request.fuzzy_search,
        enable_semantic=request.enable_semantic,
        enable_text=request.enable_text,
        enable_llm_filter=request.enable_llm_filter,
    )
    for label, result in zip(signal_labels, results):
        if isinstance(result, list):
            if label == "identifier":
                guaranteed_seeds.extend(result)
            else:
                ranked_lists.append(result)
            if label == "semantic":
                meta.semantic_hits = len(result)
                distances = [r.get("distance", 1.0) for r in result if "distance" in r]
                if distances:
                    meta.avg_semantic_distance = round(sum(distances) / len(distances), 4)
            elif label == "fuzzy":
                meta.fuzzy_hits = len(result)
            elif label == "entity":
                meta.entity_hits = len(result)
        elif isinstance(result, Exception):
            log.warning("trace_discovery_signal_failed", signal=label, error=str(result))

    log.info(
        "trace_discovery_results",
        trace_id=trace_id,
        query=request.query,
        identifier_hits=len(guaranteed_seeds),
        identifier_ids=[(s.get("card_id") or s.get("entity_id") or "?")[:30] for s in guaranteed_seeds[:5]],
        rrf_signal_count=len(ranked_lists),
        rrf_signal_sizes=[len(rl) for rl in ranked_lists],
    )

    # Merge non-identifier signals via RRF
    await _progress("Ranking results", 2, total_steps)
    loop = asyncio.get_event_loop()
    fused = await loop.run_in_executor(None, reciprocal_rank_fusion, ranked_lists, 60)

    # Build seed list: guaranteed identifier matches first, then RRF results
    seen: set[str] = set()
    seeds: list[dict] = []
    for item in guaranteed_seeds:
        uid = item.get("entity_id") or item.get("card_id") or item.get("id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            seeds.append(item)
    for item in fused:
        uid = item.get("entity_id") or item.get("card_id") or item.get("id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            seeds.append(item)
        if len(seeds) >= request.max_results:
            break

    log.info(
        "trace_seeds",
        trace_id=trace_id,
        total=len(seeds),
        seed_ids=[(s.get("card_id") or s.get("entity_id") or "?")[:30] for s in seeds[:10]],
        seed_entity_ids=[(s.get("entity_id") or "?")[:40] for s in seeds[:10]],
    )

    # Phase 1.5 — Apply trace feedback (exclude/demote previously-rejected entities)
    await _progress("Applying feedback", 3, total_steps)
    feedback = await _query_trace_feedback(request.query)
    if feedback["exact_exclude"]:
        before = len(seeds)
        seeds = [
            s for s in seeds
            if s.get("entity_id") not in feedback["exact_exclude"]
            or s.get("source") == "identifier"
        ]
        meta.feedback_excluded = before - len(seeds)
    if feedback["global_demote"]:
        priority = [s for s in seeds if s.get("entity_id") not in feedback["global_demote"]]
        demoted = [s for s in seeds if s.get("entity_id") in feedback["global_demote"]]
        meta.feedback_demoted = len(demoted)
        seeds = (priority + demoted)[:request.max_results]

    # Phase 2 — Expansion
    await _progress("Expanding results", 4, total_steps)
    all_cards, entity_map = await _expand_seeds(seeds, request.space_id, request.include_archived)
    meta.expansion_cards = len(all_cards)

    # Phase 2.5 — LLM relevance filter (remove false positives before clustering)
    await _progress("Analyzing connections", 5, total_steps)
    if request.enable_llm_filter:
        seeds, removed_count = await _llm_relevance_filter(request.query, seeds, all_cards, trace_id=trace_id, space_id=request.space_id)
        meta.seeds_filtered = removed_count
    else:
        removed_count = 0

    if removed_count > 0:
        # Remove cards whose entities are no longer backed by surviving seeds
        surviving_eids: set[str] = set()
        for s in seeds:
            eid = s.get("entity_id")
            if eid:
                surviving_eids.add(eid)
            cid = s.get("card_id")
            if cid:
                for c in all_cards:
                    if c.card_id == cid and c.entity_id:
                        surviving_eids.add(c.entity_id)
                        break
        # Keep cross-referenced entities (same subject_id)
        keep_subjects: set[str] = set()
        for eid in surviving_eids:
            parts = eid.split(":", 2)
            if len(parts) >= 3:
                keep_subjects.add(parts[2].lower())
        for c in all_cards:
            if c.entity_id:
                parts = c.entity_id.split(":", 2)
                if len(parts) >= 3 and parts[2].lower() in keep_subjects:
                    surviving_eids.add(c.entity_id)
        all_cards = [c for c in all_cards if c.entity_id in surviving_eids]

    # Phase 3 — Clustering (before capping, so small clusters aren't eliminated)
    await _progress("Building clusters", 6, total_steps)
    # Clustering is CPU-bound (union-find + chapter detection) — run in
    # executor so the event loop stays responsive for other API requests.
    clusters = await loop.run_in_executor(
        None, _build_clusters, all_cards, entity_map, seeds
    )

    # Cap cards per cluster to keep results manageable without dropping
    # entire clusters. Distribute max_results proportionally.
    if clusters:
        per_cluster = max(request.max_results // len(clusters), 5)
        for cluster in clusters:
            if len(cluster.timeline) > per_cluster:
                cluster.timeline = cluster.timeline[:per_cluster]
                cluster.status_summary.total_cards = len(cluster.timeline)

    meta.elapsed_ms = int((time.monotonic() - t0) * 1000)
    now = db_now()

    response = TraceResponse(
        trace_id=trace_id,
        query=request.query,
        clusters=clusters,
        search_metadata=meta,
        created_at=now,
        space_id=request.space_id,
    )

    # Persist trace to DB
    await _save_trace(response)

    # Announce completion over WS so a client whose HTTP request already aborted
    # (a rerun can outlast any timeout) can still recover the result by fetching
    # this trace_id. Broadcast AFTER _save_trace so any client that reacts by
    # calling GET /traces/{id} is guaranteed to find the persisted row.
    await manager.broadcast({
        "type": "trace_complete",
        "trace_id": trace_id,
        "query": request.query,
        "cluster_count": len(response.clusters),
    })

    return response


# ---------------------------------------------------------------------------
# Phase 1 — Discovery signals
# ---------------------------------------------------------------------------


async def _identifier_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """Direct identifier lookup for patterns like 'PR 540', 'LAYA-986', etc.

    Generates common variants (PR-540, PR #540, PR-540, #540) and searches
    source_ref, entity_id, and header with exact substring matching.
    This is the highest-signal search — if it finds matches, they're almost
    certainly what the user is looking for.
    """
    matches = _IDENTIFIER_RE.findall(query)
    if not matches:
        return []

    db = await get_db()
    all_results: list[dict] = []

    for prefix, number in matches:
        # Generate common identifier variants
        variants = [
            f"{prefix}-{number}",    # PR-540, LAYA-986
            f"{prefix} #{number}",   # PR #540
            f"{prefix}#{number}",    # PR#540
            f"#{number}",            # #540
            f"{prefix} {number}",    # PR 540
        ]
        # Also uppercase variant
        up = prefix.upper()
        if up != prefix:
            variants.extend([
                f"{up}-{number}",
                f"{up} #{number}",
                f"{up}#{number}",
            ])

        conditions: list[str] = []
        params: list[str] = []
        for v in variants:
            conditions.append(
                "(c.source_ref LIKE ? OR c.entity_id LIKE ? OR c.header LIKE ?)"
            )
            params.extend([f"%{v}%"] * 3)

        where = " OR ".join(conditions)
        extra = ""
        if space_id:
            extra = " AND c.space_id = ?"
            params.append(space_id)
        params.append(str(n))

        rows = await db.execute_fetchall(
            f"""SELECT c.card_id, c.entity_id, c.source_ref, c.header
                FROM action_cards c
                WHERE ({where}){extra}
                ORDER BY c.created_at DESC LIMIT ?""",
            params,
        )
        for row in rows:
            all_results.append({
                "id": row["card_id"],
                "card_id": row["card_id"],
                "entity_id": row["entity_id"] or "",
                "source": "identifier",
            })

    return all_results[:n]


async def _semantic_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """ChromaDB semantic search on card embeddings."""
    where = {"space_id": space_id} if space_id else None
    results = await memory_search(query, n_results=n, where=where, max_distance=0.65)
    return [
        {
            "id": r["metadata"].get("card_id", r["id"]),
            "card_id": r["metadata"].get("card_id"),
            # Real grouping key, not the entity_refs CSV that used to sit here and
            # broke dedup + feedback exclusion for semantic seeds (review §2 — P4-4).
            # Pre-fix embeds lack this key and fall back to "" (still better than a
            # wrong value); new embeds carry it (see emit._embed_card metadata).
            "entity_id": r["metadata"].get("entity_id", ""),
            "source": "semantic",
            "distance": r.get("distance", 1.0),
        }
        for r in results
    ]


async def _card_text_search(
    query: str, space_id: str | None, n: int, include_archived: bool = True
) -> list[dict]:
    """SQLite phrase-match search — matches the full query as a substring."""
    db = await get_db()
    phrase = query.strip()
    if len(phrase) < 2:
        return []

    fields = ["c.header", "c.summary", "c.source_ref", "c.entity_id", "c.source_url"]
    condition = " OR ".join(f"{f} LIKE ?" for f in fields)
    params: list[str] = [f"%{phrase}%"] * len(fields)

    extra = ""
    if space_id:
        extra += " AND c.space_id = ?"
        params.append(space_id)
    if not include_archived:
        extra += " AND c.status != 'archived'"

    # Boost: phrase in header/source_ref ranks higher
    boost = "(CASE WHEN c.header LIKE ? OR c.source_ref LIKE ? THEN 1 ELSE 0 END)"
    boost_params = [f"%{phrase}%"] * 2

    all_params = boost_params + params + [str(n)]

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id, c.source_ref, c.header, c.priority,
                   ({boost}) AS relevance
            FROM action_cards c
            WHERE ({condition}){extra}
            ORDER BY relevance DESC, c.created_at DESC LIMIT ?""",
        all_params,
    )
    return [
        {
            "id": row["card_id"],
            "card_id": row["card_id"],
            "entity_id": row["entity_id"] or "",
            "source": "text",
        }
        for row in rows
    ]


async def _card_fuzzy_search(
    query: str, space_id: str | None, n: int, include_archived: bool = True
) -> list[dict]:
    """SQLite keyword-split search — each keyword must appear somewhere (broad matching)."""
    db = await get_db()
    keywords = extract_keywords(query, min_len=2)
    if not keywords:
        return []

    # Each keyword must match at least one searchable field (AND across keywords)
    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:8]:
        conditions.append(
            "(c.header LIKE ? OR c.summary LIKE ? OR c.source_ref LIKE ? "
            "OR c.entity_id LIKE ? OR c.source_url LIKE ?)"
        )
        params.extend([f"%{kw}%"] * 5)

    where = " AND ".join(conditions)
    extra = ""
    if space_id:
        extra += " AND c.space_id = ?"
        params.append(space_id)
    if not include_archived:
        extra += " AND c.status != 'archived'"

    # Build exact-match boost: cards where keywords appear in header/source_ref
    # score higher (sorted first) vs those matching only in summary/body
    boost_parts: list[str] = []
    boost_params: list[str] = []
    for kw in keywords[:8]:
        boost_parts.append("(CASE WHEN c.header LIKE ? OR c.source_ref LIKE ? THEN 1 ELSE 0 END)")
        boost_params.extend([f"%{kw}%"] * 2)

    boost_expr = " + ".join(boost_parts) if boost_parts else "0"

    all_params = boost_params + params + [str(n)]

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id, c.source_ref, c.header, c.priority,
                   ({boost_expr}) AS relevance
            FROM action_cards c
            WHERE ({where}){extra}
            ORDER BY relevance DESC, c.created_at DESC LIMIT ?""",
        all_params,
    )
    return [
        {
            "id": row["card_id"],
            "card_id": row["card_id"],
            "entity_id": row["entity_id"] or "",
            "source": "fuzzy",
        }
        for row in rows
    ]


async def _entity_table_search(query: str, n: int) -> list[dict]:
    """Search the entities table by canonical_name and platform_refs."""
    db = await get_db()
    keywords = extract_keywords(query, min_len=2)
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:5]:
        conditions.append("(canonical_name LIKE ? OR platform_refs LIKE ?)")
        params.extend([f"%{kw}%"] * 2)

    where = " OR ".join(conditions)
    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT entity_id, entity_type, canonical_name, platform_refs, confidence
            FROM entities WHERE {where}
            ORDER BY confidence DESC LIMIT ?""",
        params,
    )
    return [
        {
            "id": row["entity_id"],
            "entity_id": row["entity_id"],
            "entity_type": row["entity_type"],
            "canonical_name": row["canonical_name"],
            "platform_refs": row["platform_refs"],
            "source": "entity",
        }
        for row in rows
    ]


async def _event_keyword_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """Keyword search on events mapped back to cards — FTS5/BM25 or LIKE fallback.

    Only this trace signal moves to FTS: it is the clean analog of chat's event
    search. The card-side trace searches (_identifier_search, _card_text_search,
    _card_fuzzy_search) stay on LIKE — they match identifier columns (source_ref,
    entity_id, source_url) that are not in cards_fts and use bespoke boost/phrase
    semantics tuned for the RRF ensemble.
    """
    return await fts_or_like(
        query,
        min_len=2,
        max_terms=5,
        fts=lambda m: _event_keyword_search_fts(m, space_id, n),
        like=lambda q: _event_keyword_search_like(q, space_id, n),
        warn_event="trace_events_fts_failed_fallback_like",
    )


async def _event_keyword_search_fts(match: str, space_id: str | None, n: int) -> list[dict]:
    """BM25-ranked event search over events_fts, mapped back to cards."""
    db = await get_db()
    where = "events_fts MATCH ?"
    params: list = [match]
    if space_id:
        where += " AND e.space_id = ?"
        params.append(space_id)
    params.append(n)

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id
            FROM events_fts
            JOIN events e ON e.event_id = events_fts.event_id
            JOIN action_cards c ON c.event_id = e.event_id
            WHERE {where}
            ORDER BY bm25(events_fts) LIMIT ?""",
        params,
    )
    # A card can have several matching events; keep its best-ranked occurrence
    # (DISTINCT in SQL is incompatible with ORDER BY bm25 here, so dedup in Python).
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        cid = row["card_id"]
        if cid in seen:
            continue
        seen.add(cid)
        out.append({
            "id": cid,
            "card_id": cid,
            "entity_id": row["entity_id"] or "",
            "source": "event_keyword",
        })
    return out


async def _event_keyword_search_like(query: str, space_id: str | None, n: int) -> list[dict]:
    """SQLite LIKE keyword search on events (fallback when FTS5 is unavailable)."""
    db = await get_db()
    keywords = extract_keywords(query, min_len=2)
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:5]:
        conditions.append("(e.subject_title LIKE ? OR e.content_body LIKE ?)")
        params.extend([f"%{kw}%"] * 2)

    where = " OR ".join(conditions)
    extra = ""
    if space_id:
        extra = " AND e.space_id = ?"
        params.append(space_id)
    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT DISTINCT c.card_id, c.entity_id
            FROM events e
            JOIN action_cards c ON c.event_id = e.event_id
            WHERE ({where}){extra}
            ORDER BY e.timestamp DESC LIMIT ?""",
        params,
    )
    return [
        {
            "id": row["card_id"],
            "card_id": row["card_id"],
            "entity_id": row["entity_id"] or "",
            "source": "event_keyword",
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# RRF fusion (same algorithm as chat.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 2 — Expansion
# ---------------------------------------------------------------------------

_CARD_SELECT = f"""
    SELECT {CARD_SELECT_COLUMNS}
    FROM action_cards c
    LEFT JOIN events e ON c.event_id = e.event_id
    LEFT JOIN spaces s ON c.space_id = s.space_id
"""


async def _expand_seeds(
    seeds: list[dict],
    space_id: str | None,
    include_archived: bool,
) -> tuple[list[CardResponse], dict[str, TraceEntity]]:
    """Expand seed results to all related cards + build entity map."""
    db = await get_db()

    # Collect unique entity_ids from seeds
    entity_ids: set[str] = set()
    card_ids: set[str] = set()
    for seed in seeds:
        eid = seed.get("entity_id")
        if eid and not eid.startswith("singleton:"):
            entity_ids.add(eid)
        cid = seed.get("card_id")
        if cid:
            card_ids.add(cid)

    # Also fetch entity_ids from the seed card_ids we found
    if card_ids:
        placeholders = ",".join("?" * len(card_ids))
        rows = await db.execute_fetchall(
            f"SELECT DISTINCT entity_id FROM action_cards WHERE card_id IN ({placeholders}) AND entity_id IS NOT NULL",
            list(card_ids),
        )
        for row in rows:
            if row["entity_id"]:
                entity_ids.add(row["entity_id"])

    # Cross-reference expansion: find linked entities
    linked_entity_ids = await _find_linked_entities(db, entity_ids)
    all_entity_ids = entity_ids | linked_entity_ids

    # Fetch ALL cards for these entity_ids
    all_cards: list[CardResponse] = []
    entity_map: dict[str, TraceEntity] = {}

    if all_entity_ids:
        placeholders = ",".join("?" * len(all_entity_ids))
        where_parts = [f"c.entity_id IN ({placeholders})"]
        params: list[str] = list(all_entity_ids)

        if space_id:
            where_parts.append("c.space_id = ?")
            params.append(space_id)
        if not include_archived:
            where_parts.append("c.status != 'archived'")

        where_clause = " AND ".join(where_parts)
        rows = await db.execute_fetchall(
            f"{_CARD_SELECT} WHERE {where_clause} ORDER BY c.created_at ASC",
            params,
        )
        for row in rows:
            all_cards.append(_row_to_card(row))

    # Also include any seed cards that weren't captured by entity expansion
    existing_card_ids = {c.card_id for c in all_cards}
    missing_card_ids = card_ids - existing_card_ids
    if missing_card_ids:
        placeholders = ",".join("?" * len(missing_card_ids))
        rows = await db.execute_fetchall(
            f"{_CARD_SELECT} WHERE c.card_id IN ({placeholders}) ORDER BY c.created_at ASC",
            list(missing_card_ids),
        )
        for row in rows:
            all_cards.append(_row_to_card(row))

    # Sort all cards chronologically
    all_cards.sort(key=lambda c: c.created_at or "")

    # Build entity map from event metadata
    seen_entities: set[str] = set()
    for card in all_cards:
        eid = card.entity_id
        if eid and eid not in seen_entities:
            seen_entities.add(eid)
            # Parse platform from entity_id format: "platform:subject_type:subject_id"
            parts = eid.split(":", 2)
            platform = parts[0] if parts else ""
            entity_map[eid] = TraceEntity(
                entity_id=eid,
                title=card.source_ref or card.header,
                url=card.source_url,
                platform=platform,
            )

    return all_cards, entity_map


# Bounds for _find_linked_entities so a hub entity can't explode into an
# O(entities × refs) storm of full-table LIKE scans (review §4 — P5-6).
_MAX_LINK_SUBJECTS = 25
_MAX_LINK_REFS = 50


async def _find_linked_entities(db, entity_ids: set[str]) -> set[str]:
    """Find cross-referenced entities via the entities table.

    The entities lookup is batched into a single query (was one per entity_id)
    and the ref-id fan-out is deduped and capped, avoiding a nested N+1 of
    per-ref full-table LIKE scans (review §4 — P5-6).
    """
    if not entity_ids:
        return set()

    # Extract subject_ids (e.g. "BUG-1234" from "jira:ticket:BUG-1234").
    subject_ids = []
    for eid in entity_ids:
        parts = eid.split(":", 2)
        subject_id = parts[-1] if parts else eid
        if len(subject_id) >= 3:
            subject_ids.append(subject_id)
    if not subject_ids:
        return set()

    linked: set[str] = set()

    # One entities query for all subjects instead of one per entity_id.
    clauses: list[str] = []
    params: list = []
    for sid in subject_ids[:_MAX_LINK_SUBJECTS]:
        clauses.append("platform_refs LIKE ? OR canonical_name LIKE ?")
        params.extend([f"%{sid}%", f"%{sid}%"])
    rows = await db.execute_fetchall(
        f"SELECT entity_id, platform_refs FROM entities WHERE {' OR '.join(clauses)}",
        params,
    )

    ref_ids: set[str] = set()
    for row in rows:
        linked.add(row["entity_id"])
        try:
            refs = json.loads(row["platform_refs"]) if row["platform_refs"] else {}
            for _platform, rid_list in refs.items():
                if isinstance(rid_list, list):
                    for rid in rid_list:
                        if rid and len(str(rid)) >= 3:
                            ref_ids.add(str(rid))
        except (json.JSONDecodeError, TypeError):
            pass

    # Deduped, capped ref fan-out (the final result is a set, so dedup is lossless).
    for rid in list(ref_ids)[:_MAX_LINK_REFS]:
        card_rows = await db.execute_fetchall(
            "SELECT DISTINCT entity_id FROM action_cards WHERE entity_id LIKE ? LIMIT 5",
            (f"%{rid}%",),
        )
        for cr in card_rows:
            if cr["entity_id"]:
                linked.add(cr["entity_id"])

    return linked - entity_ids  # Only return newly discovered ones


# ---------------------------------------------------------------------------
# Phase 3 — Clustering and Chapter detection
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# Display labels per raw_event_type (a different value space from terminal-ness;
# the terminal-event source of truth lives in egress/registry._TERMINAL_EVENT_TYPES —
# keep these in mind together when adding/renaming platform event types).
_CHAPTER_LABELS = {
    # event_type hints
    "issue_created": "Created",
    "pr_created": "Created",
    "message_sent": "Discussion",
    "email_received": "Discussion",
    "pr_commented": "Code Review",
    "issue_commented": "Discussion",
    "pr_approved": "Approved",
    "pr_merged": "Merged",
    "issue_resolved": "Resolved",
    "issue_status_changed": "Status Change",
    "issue_reopened": "Reopened",
    "build_completed": "Build",
    "build_failed": "Build Failed",
    "pr_declined": "Declined",
}

from laya.egress.registry import get_chapter_default as _get_chapter_default


def _build_clusters(
    all_cards: list[CardResponse],
    entity_map: dict[str, TraceEntity],
    seeds: list[dict],
) -> list[TraceCluster]:
    """Group cards into clusters by connected entity_ids."""
    if not all_cards:
        return []

    # Union-Find to group connected entity_ids
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # All entity_ids from cards
    card_entity_ids = {c.entity_id for c in all_cards if c.entity_id}

    # Initialize parent
    for eid in card_entity_ids:
        parent[eid] = eid

    # Union entities that share the same subject_id or are in entity_map links
    entity_subjects: dict[str, list[str]] = {}
    for eid in card_entity_ids:
        parts = eid.split(":", 2)
        if len(parts) >= 3:
            subject = parts[2].lower()
            entity_subjects.setdefault(subject, []).append(eid)

    for _subject, eids in entity_subjects.items():
        for i in range(1, len(eids)):
            union(eids[0], eids[i])

    # Group cards by cluster root
    cluster_cards: dict[str, list[CardResponse]] = {}
    for card in all_cards:
        eid = card.entity_id or f"singleton:{card.card_id}"
        root = find(eid) if eid in parent else eid
        cluster_cards.setdefault(root, []).append(card)

    # Build TraceCluster objects
    clusters: list[TraceCluster] = []
    for root, cards in cluster_cards.items():
        # Identify primary entity (most cards)
        entity_counts: dict[str, int] = {}
        for c in cards:
            if c.entity_id:
                entity_counts[c.entity_id] = entity_counts.get(c.entity_id, 0) + 1

        primary_eid = max(entity_counts, key=entity_counts.get) if entity_counts else root
        primary = entity_map.get(primary_eid, TraceEntity(
            entity_id=primary_eid,
            title=cards[0].source_ref or cards[0].header,
            url=cards[0].source_url,
            platform=primary_eid.split(":")[0] if ":" in primary_eid else "",
        ))

        linked = [
            entity_map.get(eid, TraceEntity(
                entity_id=eid, title=eid, platform=eid.split(":")[0] if ":" in eid else ""
            ))
            for eid in entity_counts
            if eid != primary_eid
        ]

        # Build chapters
        chapters = _detect_chapters(cards)

        # Build status summary
        platforms = list({c.entity_id.split(":")[0] for c in cards if c.entity_id and ":" in c.entity_id})
        dates = [c.created_at for c in cards if c.created_at]
        pending = sum(
            1 for c in cards if c.status in ("pending", "ready", "awaiting_input")
        )

        latest_card = cards[-1]
        current_state = latest_card.status
        if latest_card.source_ref:
            current_state = f"{latest_card.status} ({latest_card.source_ref})"

        status_summary = TraceStatusSummary(
            current_state=current_state,
            platforms_involved=sorted(platforms),
            total_cards=len(cards),
            date_range={
                "from": min(dates)[:10] if dates else "",
                "to": max(dates)[:10] if dates else "",
            },
            pending_actions=pending,
        )

        clusters.append(TraceCluster(
            cluster_id=f"cluster_{uuid.uuid4().hex[:8]}",
            primary_entity=primary,
            linked_entities=linked,
            chapters=chapters,
            timeline=cards,
            status_summary=status_summary,
        ))

    # Sort clusters: largest first
    clusters.sort(key=lambda c: c.status_summary.total_cards, reverse=True)
    return clusters


def _detect_chapters(cards: list[CardResponse]) -> list[TraceChapter]:
    """Group chronological cards into logical chapters."""
    if not cards:
        return []

    chapters: list[TraceChapter] = []
    current_label = ""
    current_cards: list[str] = []
    current_ts = ""
    last_time: datetime | None = None

    for card in cards:
        label = _infer_chapter_label(card, is_first=(len(chapters) == 0 and not current_cards))

        # Detect time gap > 24 hours
        card_time = None
        if card.created_at:
            try:
                card_time = datetime.fromisoformat(card.created_at.replace("Z", "+00:00"))
            except ValueError:
                pass

        time_gap = False
        if last_time and card_time:
            gap_hours = (card_time - last_time).total_seconds() / 3600
            time_gap = gap_hours > 24

        # Start new chapter if label changes or time gap
        if label != current_label or time_gap:
            if current_cards:
                chapters.append(TraceChapter(
                    label=current_label,
                    timestamp=current_ts,
                    card_ids=current_cards,
                ))
            current_label = label
            current_cards = [card.card_id]
            current_ts = card.created_at or ""
        else:
            current_cards.append(card.card_id)

        if card_time:
            last_time = card_time

    # Flush last chapter
    if current_cards:
        chapters.append(TraceChapter(
            label=current_label,
            timestamp=current_ts,
            card_ids=current_cards,
        ))

    return chapters


def _infer_chapter_label(card: CardResponse, is_first: bool = False) -> str:
    """Infer a human-readable chapter label from card metadata."""
    if is_first:
        return "Created"

    # Try to infer from entity_id platform
    platform = ""
    if card.entity_id and ":" in card.entity_id:
        platform = card.entity_id.split(":")[0]

    # Check status for terminal states
    if card.status in ("done", "dismissed"):
        return "Resolved"
    if card.status == "failed":
        return "Failed"
    if card.status == "archived":
        return "Archived"

    # Use platform defaults
    return _get_chapter_default(platform)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def _save_trace(response: TraceResponse) -> None:
    """Persist a trace to the database."""
    db = await get_db()

    card_ids = []
    chapters_json = []
    cluster_data = []
    for cluster in response.clusters:
        card_ids.extend(c.card_id for c in cluster.timeline)
        chapters_json.extend(ch.model_dump() for ch in cluster.chapters)
        cluster_data.append({
            "cluster_id": cluster.cluster_id,
            "primary_entity": cluster.primary_entity.model_dump(),
            "linked_entities": [e.model_dump() for e in cluster.linked_entities],
            "status_summary": cluster.status_summary.model_dump(),
        })

    # Upsert (not a plain INSERT) so a rerun — which reuses the existing trace_id
    # (see run_trace) — replaces the row IN PLACE, atomically, and only on success.
    # This is a single statement, so it's inherently atomic and needs no
    # db/sqlite.transaction() wrapper (that's for multi-statement invariants);
    # keeping it single-statement is precisely what makes rerun crash-safe.
    #   - created_at is deliberately NOT in DO UPDATE SET: the original stands so
    #     history ordering (ORDER BY created_at DESC) is stable and a rerun doesn't
    #     jump the row to the top. updated_at moves to the new db_now() value.
    #   - narrative/summary are cleared: both describe the OLD cluster set (per-cluster
    #     narratives live inside cluster_data and are already discarded with it, but
    #     these two top-level columns must be NULLed so _reconstruct_trace doesn't
    #     render a summary narrating cards that are no longer in the trace).
    await db.execute(
        """INSERT INTO traces (trace_id, query, created_at, updated_at, chapters,
                               cluster_data, card_ids, search_metadata, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(trace_id) DO UPDATE SET
               query           = excluded.query,
               updated_at      = excluded.updated_at,
               chapters        = excluded.chapters,
               cluster_data    = excluded.cluster_data,
               card_ids        = excluded.card_ids,
               search_metadata = excluded.search_metadata,
               space_id        = excluded.space_id,
               narrative       = NULL,
               summary         = NULL""",
        (
            response.trace_id,
            response.query,
            response.created_at,
            response.created_at,
            json.dumps(chapters_json),
            json.dumps(cluster_data),
            json.dumps(card_ids),
            response.search_metadata.model_dump_json(),
            response.space_id,
        ),
    )
    await db.commit()


async def _update_cluster_narrative(
    trace_id: str, cluster_id: str, narrative: str
) -> None:
    """Persist a narrative for a specific cluster inside the trace's cluster_data JSON."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT cluster_data FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        return

    cluster_data = json.loads(rows[0]["cluster_data"]) if rows[0]["cluster_data"] else []
    for cdata in cluster_data:
        if cdata.get("cluster_id") == cluster_id:
            cdata["narrative"] = narrative
            break

    await db.execute(
        "UPDATE traces SET cluster_data = ?, updated_at = ? WHERE trace_id = ?",
        (json.dumps(cluster_data), db_now(), trace_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Narrative streaming
# ---------------------------------------------------------------------------


async def _stream_cluster_narrative(
    trace_id: str, cluster: TraceCluster, space_id: str | None = None
) -> None:
    """Generate and stream a narrative for a single cluster via WebSocket.

    Acquires the shared pipeline semaphore so trace narratives respect
    the same concurrency limit as feed card generation.
    """
    sem = _get_semaphore()
    async with sem:
        await _stream_cluster_narrative_inner(trace_id, cluster, space_id=space_id)


async def _stream_cluster_narrative_inner(
    trace_id: str, cluster: TraceCluster, space_id: str | None = None
) -> None:
    """Inner narrative generation (called under semaphore)."""
    cluster_id = cluster.cluster_id
    full_narrative = ""
    try:
        messages = build_narrative_messages([cluster], user_identity=get_self_user())

        await manager.broadcast({
            "type": "trace_narrative_start",
            "trace_id": trace_id,
            "cluster_id": cluster_id,
        })

        async for event in llm_call_streaming(
            role="trace",
            messages=messages,
            step="trace",
            temperature=0.3,
            max_tokens=DEFAULT_MAX_TOKENS,
            space_id=space_id,
        ):
            if event.type == "chunk" and event.content:
                full_narrative += event.content
                await manager.broadcast({
                    "type": "trace_narrative_chunk",
                    "trace_id": trace_id,
                    "cluster_id": cluster_id,
                    "content": event.content,
                })
            elif event.type == "error":
                log.error(
                    "trace_narrative_error",
                    trace_id=trace_id, cluster_id=cluster_id, error=event.content,
                )
                break

        # Persist per-cluster narrative
        await _update_cluster_narrative(trace_id, cluster_id, full_narrative)
    except Exception as e:
        log.error(
            "trace_narrative_inner_error",
            trace_id=trace_id, cluster_id=cluster_id, error=str(e),
        )
    finally:
        await manager.broadcast({
            "type": "trace_narrative_done",
            "trace_id": trace_id,
            "cluster_id": cluster_id,
            "narrative": full_narrative,
        })

    log.info(
        "trace_narrative_complete",
        trace_id=trace_id, cluster_id=cluster_id, length=len(full_narrative),
    )


async def stream_trace_narrative(
    trace_id: str, clusters: list[TraceCluster], space_id: str | None = None
) -> None:
    """Generate and stream narratives for each cluster independently."""
    try:
        # Run narratives for all clusters concurrently
        await asyncio.gather(
            *(_stream_cluster_narrative(trace_id, c, space_id=space_id) for c in clusters)
        )
    except Exception as e:
        log.error("trace_narrative_failed", trace_id=trace_id, error=str(e))


async def stream_trace_summary(
    trace_id: str, query: str, clusters: list[TraceCluster],
    space_id: str | None = None,
) -> None:
    """Generate and stream an overall summary across all clusters via WebSocket."""
    sem = _get_semaphore()
    async with sem:
        full_text = ""
        summary_id = "__summary__"
        try:
            messages = build_summary_messages(query, clusters, user_identity=get_self_user())

            await manager.broadcast({
                "type": "trace_narrative_start",
                "trace_id": trace_id,
                "cluster_id": summary_id,
            })

            async for event in llm_call_streaming(
                role="trace",
                messages=messages,
                step="trace_summary",
                temperature=0.3,
                max_tokens=DEFAULT_MAX_TOKENS,
                space_id=space_id,
            ):
                if event.type == "chunk" and event.content:
                    full_text += event.content
                    await manager.broadcast({
                        "type": "trace_narrative_chunk",
                        "trace_id": trace_id,
                        "cluster_id": summary_id,
                        "content": event.content,
                    })
                elif event.type == "error":
                    log.error("trace_summary_error", trace_id=trace_id, error=event.content)
                    break

            # Persist summary to the trace record
            db = await get_db()
            await db.execute(
                "UPDATE traces SET summary = ? WHERE trace_id = ?",
                (full_text, trace_id),
            )
            await db.commit()

        except Exception as e:
            log.error("trace_summary_failed", trace_id=trace_id, error=str(e))
        finally:
            await manager.broadcast({
                "type": "trace_narrative_done",
                "trace_id": trace_id,
                "cluster_id": summary_id,
                "narrative": full_text,
            })

        log.info("trace_summary_complete", trace_id=trace_id, length=len(full_text))
