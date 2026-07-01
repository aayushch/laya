# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""REST API for the Trace feature (semantic cross-platform entity search)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from laya.api.cards_api import _row_to_card
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.models.card import CardResponse
from laya.models.trace import (
    SearchMetadata,
    TraceChapter,
    TraceCluster,
    TraceEntity,
    TraceListItem,
    TraceRequest,
    TraceResponse,
    TraceStatusSummary,
)
from laya.pipeline.trace import run_trace, stream_trace_narrative, stream_trace_summary, request_cancel, TraceCancelled

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /trace — run a new trace
# ---------------------------------------------------------------------------


@router.post("/trace")
async def create_trace(request: TraceRequest) -> TraceResponse:
    """Run a new trace: returns clusters immediately, narrative generated on demand."""
    log.info("trace_requested", query=request.query, space_id=request.space_id)

    try:
        response = await run_trace(request)
    except TraceCancelled:
        raise HTTPException(status_code=499, detail="Trace cancelled")
    return response


@router.post("/trace/cancel")
async def cancel_trace() -> dict:
    """Cancel any currently running trace."""
    from laya.pipeline.trace import _cancel_events  # noqa: avoid circular at module level
    cancelled = []
    for tid in list(_cancel_events.keys()):
        if request_cancel(tid):
            cancelled.append(tid)
    return {"cancelled": cancelled}


# ---------------------------------------------------------------------------
# GET /traces — list past traces
# ---------------------------------------------------------------------------


@router.get("/traces")
async def list_traces(limit: int = 20, offset: int = 0) -> list[TraceListItem]:
    """List past traces, most recent first."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT trace_id, query, created_at, card_ids, cluster_data, search_metadata "
        "FROM traces ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )

    items: list[TraceListItem] = []
    for row in rows:
        card_ids = []
        platforms: list[str] = []
        fuzzy_search = False
        try:
            card_ids = json.loads(row["card_ids"]) if row["card_ids"] else []
        except json.JSONDecodeError:
            pass
        try:
            cluster_data = json.loads(row["cluster_data"]) if row["cluster_data"] else []
            for cluster in cluster_data:
                summary = cluster.get("status_summary", {})
                platforms.extend(summary.get("platforms_involved", []))
        except json.JSONDecodeError:
            pass
        try:
            search_meta = json.loads(row["search_metadata"]) if row["search_metadata"] else {}
            fuzzy_search = search_meta.get("fuzzy_search", False)
        except json.JSONDecodeError:
            search_meta = {}

        items.append(TraceListItem(
            trace_id=row["trace_id"],
            query=row["query"],
            created_at=row["created_at"],
            total_cards=len(card_ids),
            platforms=sorted(set(platforms)),
            fuzzy_search=fuzzy_search,
            enable_semantic=search_meta.get("enable_semantic", True),
            enable_text=search_meta.get("enable_text", True),
            enable_llm_filter=search_meta.get("enable_llm_filter", True),
        ))

    return items


# ---------------------------------------------------------------------------
# GET /traces/{trace_id} — get a saved trace
# ---------------------------------------------------------------------------


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> TraceResponse:
    """Retrieve a saved trace with cached narrative and full card data."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    return await _reconstruct_trace(db, rows[0])


# ---------------------------------------------------------------------------
# POST /traces/{trace_id}/rerun — re-run a trace
# ---------------------------------------------------------------------------


@router.post("/traces/{trace_id}/rerun")
async def rerun_trace(trace_id: str) -> TraceResponse:
    """Re-run a trace with the original query (picks up new cards)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT query, space_id FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")
    row = rows[0]

    # Delete old trace
    await db.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
    await db.commit()

    # Run fresh trace
    request = TraceRequest(query=row["query"], space_id=row["space_id"])
    response = await run_trace(request)
    return response


# ---------------------------------------------------------------------------
# POST /traces/{trace_id}/clusters/{cluster_id}/narrative — generate narrative
# ---------------------------------------------------------------------------


@router.post("/traces/{trace_id}/clusters/{cluster_id}/narrative")
async def generate_cluster_narrative(trace_id: str, cluster_id: str) -> dict:
    """Manually trigger narrative generation for a specific cluster (streams via WS)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    trace = await _reconstruct_trace(db, rows[0])
    cluster = next((c for c in trace.clusters if c.cluster_id == cluster_id), None)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    # Fire-and-forget: stream narrative via WebSocket
    from laya.tasks import create_task as create_tracked_task
    create_tracked_task(
        stream_trace_narrative(trace_id, [cluster], space_id=trace.space_id),
        name=f"trace_narrative_{trace_id}_{cluster_id}",
    )

    return {"status": "generating", "trace_id": trace_id, "cluster_id": cluster_id}


@router.post("/traces/{trace_id}/summary")
async def generate_trace_summary(trace_id: str) -> dict:
    """Generate an overall summary across all clusters in a trace (streams via WS)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    trace = await _reconstruct_trace(db, rows[0])
    if not trace.clusters:
        raise HTTPException(status_code=400, detail="No clusters to summarize")

    from laya.tasks import create_task as create_tracked_task
    create_tracked_task(
        stream_trace_summary(trace_id, trace.query, trace.clusters, space_id=trace.space_id),
        name=f"trace_summary_{trace_id}",
    )

    return {"status": "generating", "trace_id": trace_id}


# ---------------------------------------------------------------------------
# DELETE /traces/{trace_id}/clusters/{cluster_id} — remove a cluster
# ---------------------------------------------------------------------------


@router.delete("/traces/{trace_id}/clusters/{cluster_id}")
async def remove_cluster(trace_id: str, cluster_id: str) -> dict:
    """Mark a cluster as removed and persist feedback for future learning."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT cluster_data, query FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    cluster_data = json.loads(rows[0]["cluster_data"]) if rows[0]["cluster_data"] else []
    trace_query = rows[0]["query"] or ""
    found = False
    removed_cdata = None
    for cdata in cluster_data:
        if cdata.get("cluster_id") == cluster_id:
            cdata["removed"] = True
            found = True
            removed_cdata = cdata
            break

    if not found:
        raise HTTPException(status_code=404, detail="Cluster not found")

    await db.execute(
        "UPDATE traces SET cluster_data = ?, updated_at = ? WHERE trace_id = ?",
        (json.dumps(cluster_data), db_now(), trace_id),
    )

    # Persist removal feedback for learning
    if removed_cdata:
        primary = removed_cdata.get("primary_entity", {})
        all_entities = [primary] + removed_cdata.get("linked_entities", [])
        for entity in all_entities:
            eid = entity.get("entity_id", "")
            if not eid:
                continue
            await db.execute(
                """INSERT INTO trace_feedback
                   (trace_id, query, entity_id, entity_title, platform, action)
                   VALUES (?, ?, ?, ?, ?, 'removed')""",
                (trace_id, trace_query, eid,
                 entity.get("title", ""), entity.get("platform", "")),
            )

    await db.commit()
    return {"removed": cluster_id}


# ---------------------------------------------------------------------------
# POST /traces/{trace_id}/clusters/restore — restore all removed clusters
# ---------------------------------------------------------------------------


@router.post("/traces/{trace_id}/clusters/restore")
async def restore_clusters(trace_id: str) -> dict:
    """Remove the 'removed' flag from all clusters and record restoration feedback."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT cluster_data, query FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    cluster_data = json.loads(rows[0]["cluster_data"]) if rows[0]["cluster_data"] else []
    trace_query = rows[0]["query"] or ""

    # Record restoration feedback for previously-removed clusters
    for cdata in cluster_data:
        if cdata.get("removed"):
            primary = cdata.get("primary_entity", {})
            all_entities = [primary] + cdata.get("linked_entities", [])
            for entity in all_entities:
                eid = entity.get("entity_id", "")
                if not eid:
                    continue
                await db.execute(
                    """INSERT INTO trace_feedback
                       (trace_id, query, entity_id, entity_title, platform, action)
                       VALUES (?, ?, ?, ?, ?, 'restored')""",
                    (trace_id, trace_query, eid,
                     entity.get("title", ""), entity.get("platform", "")),
                )
        cdata.pop("removed", None)

    await db.execute(
        "UPDATE traces SET cluster_data = ?, updated_at = ? WHERE trace_id = ?",
        (json.dumps(cluster_data), db_now(), trace_id),
    )
    await db.commit()
    return {"restored": trace_id}


# ---------------------------------------------------------------------------
# DELETE /traces/{trace_id}
# ---------------------------------------------------------------------------


@router.delete("/traces/{trace_id}")
async def delete_trace(trace_id: str) -> dict:
    """Delete a saved trace."""
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT trace_id FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Trace not found")

    await db.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
    await db.commit()
    return {"deleted": trace_id}


# ---------------------------------------------------------------------------
# GET /traces/{trace_id}/export — export as markdown
# ---------------------------------------------------------------------------


@router.get("/traces/{trace_id}/export")
async def export_trace(trace_id: str) -> PlainTextResponse:
    """Export a trace as a formatted markdown document."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    trace = await _reconstruct_trace(db, rows[0])
    md = _render_markdown(trace)

    return PlainTextResponse(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="trace-{trace_id}.md"'},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _reconstruct_trace(db, row) -> TraceResponse:
    """Reconstruct a full TraceResponse from a DB row by re-fetching card data."""
    card_ids: list[str] = []
    try:
        card_ids = json.loads(row["card_ids"]) if row["card_ids"] else []
    except json.JSONDecodeError:
        pass

    chapters_raw: list[dict] = []
    try:
        chapters_raw = json.loads(row["chapters"]) if row["chapters"] else []
    except json.JSONDecodeError:
        pass

    cluster_data_raw: list[dict] = []
    try:
        cluster_data_raw = json.loads(row["cluster_data"]) if row["cluster_data"] else []
    except json.JSONDecodeError:
        pass

    search_meta_raw: dict = {}
    try:
        search_meta_raw = json.loads(row["search_metadata"]) if row["search_metadata"] else {}
    except json.JSONDecodeError:
        pass

    # Fetch current card data
    cards_by_id: dict[str, CardResponse] = {}
    if card_ids:
        placeholders = ",".join("?" * len(card_ids))
        card_rows = await db.execute_fetchall(
            f"""SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                       c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                       c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                       c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                       c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                       c.space_id, c.bookmarked_at, c.group_active_at,
                       e.actor_name, e.actor_email,
                       s.name AS space_name, s.color AS space_color
                FROM action_cards c
                LEFT JOIN events e ON c.event_id = e.event_id
                LEFT JOIN spaces s ON c.space_id = s.space_id
                WHERE c.card_id IN ({placeholders})""",
            card_ids,
        )
        for cr in card_rows:
            card = _row_to_card(cr)
            cards_by_id[card.card_id] = card

    # Rebuild ordered timeline
    timeline = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]

    # Rebuild clusters (skip removed ones)
    clusters: list[TraceCluster] = []
    for cdata in cluster_data_raw:
        if cdata.get("removed"):
            continue
        primary = TraceEntity(**cdata["primary_entity"])
        linked = [TraceEntity(**e) for e in cdata.get("linked_entities", [])]
        summary = TraceStatusSummary(**cdata["status_summary"])

        # Map chapters to this cluster's cards
        cluster_card_ids = {c.card_id for c in timeline if _card_in_cluster(c, primary, linked)}
        cluster_chapters = [
            TraceChapter(**ch) for ch in chapters_raw
            if any(cid in cluster_card_ids for cid in ch.get("card_ids", []))
        ]
        cluster_timeline = [c for c in timeline if c.card_id in cluster_card_ids]

        # Update summary with current card count (cards may have been deleted)
        summary.total_cards = len(cluster_timeline)

        clusters.append(TraceCluster(
            cluster_id=cdata["cluster_id"],
            primary_entity=primary,
            linked_entities=linked,
            narrative=cdata.get("narrative") or row["narrative"],
            chapters=cluster_chapters,
            timeline=cluster_timeline,
            status_summary=summary,
        ))

    # Fallback: if no clusters reconstructed, put all cards in one
    if not clusters and timeline:
        platforms = list({
            c.entity_id.split(":")[0]
            for c in timeline if c.entity_id and ":" in c.entity_id
        })
        clusters.append(TraceCluster(
            cluster_id="cluster_fallback",
            primary_entity=TraceEntity(
                entity_id=timeline[0].entity_id or "",
                title=timeline[0].source_ref or timeline[0].header,
                url=timeline[0].source_url,
                platform=platforms[0] if platforms else "",
            ),
            narrative=row["narrative"],
            timeline=timeline,
            status_summary=TraceStatusSummary(
                current_state=timeline[-1].status,
                platforms_involved=sorted(platforms),
                total_cards=len(timeline),
                date_range={
                    "from": timeline[0].created_at[:10] if timeline[0].created_at else "",
                    "to": timeline[-1].created_at[:10] if timeline[-1].created_at else "",
                },
                pending_actions=sum(
                    1 for c in timeline if c.status in ("pending", "ready")
                ),
            ),
        ))

    return TraceResponse(
        trace_id=row["trace_id"],
        query=row["query"],
        clusters=clusters,
        search_metadata=SearchMetadata(**search_meta_raw),
        created_at=row["created_at"],
        summary=row["summary"] if "summary" in row.keys() else None,
        space_id=row["space_id"] if "space_id" in row.keys() else None,
    )


def _card_in_cluster(card: CardResponse, primary: TraceEntity, linked: list[TraceEntity]) -> bool:
    """Check if a card belongs to a cluster by entity_id match."""
    all_eids = {primary.entity_id} | {e.entity_id for e in linked}
    return card.entity_id in all_eids if card.entity_id else False


def _render_markdown(trace: TraceResponse) -> str:
    """Render a trace as a markdown document."""
    lines: list[str] = []
    lines.append(f'# Trace: "{trace.query}"')

    for cluster in trace.clusters:
        s = cluster.status_summary
        platforms_str = ", ".join(s.platforms_involved) if s.platforms_involved else "unknown"
        lines.append(
            f"*Generated {trace.created_at[:10]} | "
            f"{s.total_cards} events across {len(s.platforms_involved)} platforms "
            f"({platforms_str}) | "
            f"{s.date_range.get('from', '?')} to {s.date_range.get('to', '?')}*"
        )
        lines.append("")

        if cluster.narrative:
            lines.append("## Summary")
            lines.append("")
            lines.append(cluster.narrative)
            lines.append("")

        lines.append("## Timeline")
        lines.append("")

        for chapter in cluster.chapters:
            date_str = chapter.timestamp[:10] if chapter.timestamp else ""
            lines.append(f"### {chapter.label} — {date_str}")
            lines.append("")

            for card_id in chapter.card_ids:
                card = next((c for c in cluster.timeline if c.card_id == card_id), None)
                if not card:
                    continue

                platform = ""
                if card.entity_id and ":" in card.entity_id:
                    platform = card.entity_id.split(":")[0]

                actor = f" by {card.actor_name}" if card.actor_name else ""
                lines.append(f"- **[{platform.title()}] {card.header}**{actor} ({card.priority})")
                if card.summary:
                    lines.append(f"  {card.summary}")
                lines.append("")

    lines.append("---")
    lines.append(f"*Exported from Laya Trace | {trace.created_at[:10]}*")

    return "\n".join(lines)
