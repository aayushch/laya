"""Trace pipeline — semantic cross-platform entity search.

Three-phase search:
  1. Discovery  — ChromaDB semantic + SQLite fuzzy + entity lookup, merged via RRF
  2. Expansion  — fetch ALL cards for matched entities + cross-references
  3. Clustering — group by connected entities, order chronologically, detect chapters
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import structlog

from laya.api.cards_api import _row_to_card
from laya.api.websocket import manager
from laya.db.chromadb_store import memory_search
from laya.db.sqlite import get_db
from laya.llm.client import llm_call_streaming
from laya.llm.prompts.trace import build_narrative_messages
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

_STOPWORDS = frozenset({
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

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_trace(request: TraceRequest) -> TraceResponse:
    """Execute a full trace: discovery → expansion → clustering."""
    t0 = time.monotonic()
    trace_id = f"trace_{uuid.uuid4().hex[:12]}"

    # Phase 1 — Discovery
    semantic_results, fuzzy_results, entity_results, event_results = await asyncio.gather(
        _semantic_search(request.query, request.space_id, n=30),
        _card_fuzzy_search(request.query, request.space_id, n=30, include_archived=request.include_archived),
        _entity_table_search(request.query, n=20),
        _event_keyword_search(request.query, request.space_id, n=20),
        return_exceptions=True,
    )

    # Collect successful results
    ranked_lists: list[list[dict]] = []
    meta = SearchMetadata()
    for label, result in [
        ("semantic", semantic_results),
        ("fuzzy", fuzzy_results),
        ("entity", entity_results),
        ("event", event_results),
    ]:
        if isinstance(result, list):
            ranked_lists.append(result)
            if label == "semantic":
                meta.semantic_hits = len(result)
            elif label == "fuzzy":
                meta.fuzzy_hits = len(result)
            elif label == "entity":
                meta.entity_hits = len(result)
        elif isinstance(result, Exception):
            log.warning("trace_discovery_signal_failed", signal=label, error=str(result))

    # Merge via RRF
    fused = _reciprocal_rank_fusion(ranked_lists, k=60)

    # Deduplicate seed results
    seen: set[str] = set()
    seeds: list[dict] = []
    for item in fused:
        uid = item.get("card_id") or item.get("entity_id") or item.get("id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            seeds.append(item)
        if len(seeds) >= 20:
            break

    # Phase 2 — Expansion
    all_cards, entity_map = await _expand_seeds(seeds, request.space_id, request.include_archived)
    meta.expansion_cards = len(all_cards)

    # Cap results
    all_cards = all_cards[: request.max_results]

    # Phase 3 — Clustering
    clusters = _build_clusters(all_cards, entity_map, seeds)

    meta.elapsed_ms = int((time.monotonic() - t0) * 1000)
    now = datetime.now(timezone.utc).isoformat()

    response = TraceResponse(
        trace_id=trace_id,
        query=request.query,
        clusters=clusters,
        search_metadata=meta,
        created_at=now,
    )

    # Persist trace to DB
    await _save_trace(response)

    return response


# ---------------------------------------------------------------------------
# Phase 1 — Discovery signals
# ---------------------------------------------------------------------------


async def _semantic_search(query: str, space_id: str | None, n: int) -> list[dict]:
    """ChromaDB semantic search on card embeddings."""
    where = {"space_id": space_id} if space_id else None
    results = await memory_search(query, n_results=n, where=where)
    return [
        {
            "id": r["metadata"].get("card_id", r["id"]),
            "card_id": r["metadata"].get("card_id"),
            "entity_id": r["metadata"].get("entity_refs", ""),
            "source": "semantic",
            "distance": r.get("distance", 1.0),
        }
        for r in results
    ]


async def _card_fuzzy_search(
    query: str, space_id: str | None, n: int, include_archived: bool = True
) -> list[dict]:
    """SQLite LIKE search on card fields."""
    db = await get_db()
    keywords = [w for w in query.split() if len(w) >= 2 and w.lower() not in _STOPWORDS]
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:8]:
        conditions.append(
            "(c.header LIKE ? OR c.summary LIKE ? OR c.source_ref LIKE ? "
            "OR c.entity_id LIKE ? OR c.source_url LIKE ?)"
        )
        params.extend([f"%{kw}%"] * 5)

    where = " OR ".join(conditions)
    extra = ""
    if space_id:
        extra += " AND c.space_id = ?"
        params.append(space_id)
    if not include_archived:
        extra += " AND c.status != 'archived'"

    params.append(str(n))

    rows = await db.execute_fetchall(
        f"""SELECT c.card_id, c.entity_id, c.source_ref, c.header, c.priority
            FROM action_cards c
            WHERE ({where}){extra}
            ORDER BY c.created_at DESC LIMIT ?""",
        params,
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
    keywords = [w for w in query.split() if len(w) >= 2 and w.lower() not in _STOPWORDS]
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
    """SQLite keyword search on events, mapped back to cards."""
    db = await get_db()
    keywords = [w for w in query.split() if len(w) >= 2 and w.lower() not in _STOPWORDS]
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


def _reciprocal_rank_fusion(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            doc_id = item.get("id") or item.get("card_id") or item.get("entity_id") or str(rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in items:
                items[doc_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
    return [items[did] for did in sorted_ids]


# ---------------------------------------------------------------------------
# Phase 2 — Expansion
# ---------------------------------------------------------------------------

_CARD_SELECT = """
    SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
           c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
           c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
           c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
           c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
           c.space_id, c.bookmarked_at,
           e.actor_name, e.actor_email,
           s.name AS space_name, s.color AS space_color
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


async def _find_linked_entities(db, entity_ids: set[str]) -> set[str]:
    """Find cross-referenced entities via the entities table."""
    if not entity_ids:
        return set()

    linked: set[str] = set()

    # Search for entity references that mention any of our entity_ids
    for eid in entity_ids:
        # Extract the subject_id part (e.g., "BUG-1234" from "jira:ticket:BUG-1234")
        parts = eid.split(":", 2)
        subject_id = parts[-1] if parts else eid

        if len(subject_id) < 3:
            continue

        rows = await db.execute_fetchall(
            "SELECT entity_id, platform_refs FROM entities "
            "WHERE platform_refs LIKE ? OR canonical_name LIKE ?",
            (f"%{subject_id}%", f"%{subject_id}%"),
        )
        for row in rows:
            linked.add(row["entity_id"])

            # Also parse platform_refs JSON to find more entity_ids
            try:
                refs = json.loads(row["platform_refs"]) if row["platform_refs"] else {}
                for _platform, ref_ids in refs.items():
                    if isinstance(ref_ids, list):
                        for ref_id in ref_ids:
                            # Try to find cards with entity_ids containing this ref
                            card_rows = await db.execute_fetchall(
                                "SELECT DISTINCT entity_id FROM action_cards "
                                "WHERE entity_id LIKE ? LIMIT 5",
                                (f"%{ref_id}%",),
                            )
                            for cr in card_rows:
                                if cr["entity_id"]:
                                    linked.add(cr["entity_id"])
            except (json.JSONDecodeError, TypeError):
                pass

    return linked - entity_ids  # Only return newly discovered ones


# ---------------------------------------------------------------------------
# Phase 3 — Clustering and Chapter detection
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

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

_PLATFORM_CHAPTER_DEFAULTS = {
    "jira": "Update",
    "github": "Code",
    "bitbucket": "Code",
    "slack": "Discussion",
    "gmail": "Email",
    "calendar": "Meeting",
}


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
            1 for c in cards if c.status in ("pending", "ready", "requires_approval", "awaiting_input")
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
    return _PLATFORM_CHAPTER_DEFAULTS.get(platform, "Update")


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

    await db.execute(
        """INSERT INTO traces (trace_id, query, created_at, updated_at, chapters,
                               cluster_data, card_ids, search_metadata, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            response.trace_id,
            response.query,
            response.created_at,
            response.created_at,
            json.dumps(chapters_json),
            json.dumps(cluster_data),
            json.dumps(card_ids),
            response.search_metadata.model_dump_json(),
            response.clusters[0].status_summary.platforms_involved[0]
            if response.clusters and response.clusters[0].status_summary.platforms_involved
            else None,
        ),
    )
    await db.commit()


async def update_trace_narrative(trace_id: str, narrative: str) -> None:
    """Update a trace with the completed narrative."""
    db = await get_db()
    await db.execute(
        "UPDATE traces SET narrative = ?, updated_at = ? WHERE trace_id = ?",
        (narrative, datetime.now(timezone.utc).isoformat(), trace_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Narrative streaming
# ---------------------------------------------------------------------------


async def stream_trace_narrative(trace_id: str, clusters: list[TraceCluster]) -> None:
    """Generate and stream a narrative summary via WebSocket, then persist."""
    try:
        messages = build_narrative_messages(clusters)

        await manager.broadcast({
            "type": "trace_narrative_start",
            "trace_id": trace_id,
        })

        full_narrative = ""
        async for event in llm_call_streaming(
            role="trace",
            messages=messages,
            step="trace",
            temperature=0.3,
            max_tokens=1000,
        ):
            if event.type == "chunk" and event.content:
                full_narrative += event.content
                await manager.broadcast({
                    "type": "trace_narrative_chunk",
                    "trace_id": trace_id,
                    "content": event.content,
                })
            elif event.type == "error":
                log.error("trace_narrative_error", trace_id=trace_id, error=event.content)
                break

        # Persist the completed narrative
        await update_trace_narrative(trace_id, full_narrative)

        await manager.broadcast({
            "type": "trace_narrative_done",
            "trace_id": trace_id,
            "narrative": full_narrative,
        })

        log.info("trace_narrative_complete", trace_id=trace_id, length=len(full_narrative))

    except Exception as e:
        log.error("trace_narrative_failed", trace_id=trace_id, error=str(e))
        await manager.broadcast({
            "type": "trace_narrative_done",
            "trace_id": trace_id,
            "narrative": "",
        })
