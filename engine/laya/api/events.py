# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Events API — Accept, validate, store, enqueue, and recover Laya Events."""

import json
from typing import Optional

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from laya.api.audit_api import utc_cutoff
from laya.db.sqlite import get_db
from laya.models.event import EventResponse, LayaEvent
from laya.pipeline.queue import enqueue_event

log = structlog.get_logger()
router = APIRouter()


# ── request / response models for dead event recovery ────────────────────

class RetryDeadEventsRequest(BaseModel):
    event_ids: Optional[list[str]] = None
    all: bool = False


class RetryDeadEventsResponse(BaseModel):
    retried: int


@router.post("/events", response_model=EventResponse, status_code=202)
async def receive_event(event: LayaEvent) -> EventResponse:
    """Receive a normalized event from n8n, store it, and enqueue for processing.

    Processing is fully decoupled — the queue consumer picks up events
    asynchronously with concurrency control and retry logic.
    """
    db = await get_db()

    # Store in SQLite — ignore duplicates (n8n may re-deliver on retry)
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO events (
            event_id, timestamp, source_platform, source_connection_id,
            source_raw_event_type, actor_name, actor_email, actor_handle,
            subject_type, subject_id, subject_title, subject_url,
            content_body, content_metadata, raw_json,
            processing_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
        """,
        (
            event.event_id,
            event.timestamp.isoformat(),
            event.source.platform,
            event.source.connection_id,
            event.source.raw_event_type,
            event.actor.name,
            event.actor.email,
            event.actor.platform_handle,
            event.subject.type,
            event.subject.id,
            event.subject.title,
            event.subject.url,
            event.content.body,
            json.dumps(event.content.metadata),
            event.model_dump_json(),
        ),
    )
    await db.commit()

    if cursor.rowcount == 0:
        # Event already exists — check if it completed the pipeline
        rows = await db.execute_fetchall(
            "SELECT processing_status FROM events WHERE event_id = ?",
            (event.event_id,),
        )
        if rows:
            status = rows[0]["processing_status"]
            if status in ("completed", "filtered"):
                log.info("event_duplicate_skipped", event_id=event.event_id)
                return EventResponse(event_id=event.event_id)
            if status == "dead":
                # Previously exhausted retries — re-enqueue on explicit re-delivery
                await enqueue_event(event.event_id)
                log.info("event_requeued_from_dead", event_id=event.event_id)
                return EventResponse(event_id=event.event_id)
            # Still queued/processing/retrying — no action needed
            log.info("event_already_in_queue", event_id=event.event_id, status=status)
            return EventResponse(event_id=event.event_id)

    log.info(
        "event_stored",
        event_id=event.event_id,
        platform=event.source.platform,
        subject=event.subject.title,
    )

    return EventResponse(event_id=event.event_id)


# ── event counts (audit page summary) ───────────────────────────────────

@router.get("/events/counts")
async def get_event_counts() -> dict:
    """Return total event count grouped by processing_status.

    Used by the Audit page to show a live breakdown of pipeline state.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT processing_status, COUNT(*) AS count FROM events GROUP BY processing_status"
    )
    counts = {r["processing_status"]: r["count"] for r in rows}
    total = sum(counts.values())
    return {"counts": counts, "total": total}


# ── filtered events (audit page, informational) ─────────────────────────

@router.get("/events/filtered")
async def list_filtered_events(limit: int = 25, offset: int = 0) -> dict:
    """List events dropped by a filter rule (terminal, no card produced).

    Purely informational for the Audit page. Unlike dead events these are
    not failures — they have no retry action and no bearing on the
    audit failure / red-dot indicator.
    """
    db = await get_db()

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) as total FROM events WHERE processing_status = 'filtered'"
    )
    total = count_rows[0]["total"] if count_rows else 0

    rows = await db.execute_fetchall(
        """SELECT event_id, timestamp, source_platform, subject_type,
                  subject_title, subject_url, actor_name, filter_rule, created_at
           FROM events
           WHERE processing_status = 'filtered'
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )

    return {
        "events": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/events/filtered/export")
async def export_filtered_events(days: int = 0) -> dict:
    """Export filtered events as JSON, optionally limited to the last `days`.

    days=0 (default) exports all time. Mirrors the filtered events list but
    unpaginated and with richer columns for offline inspection.
    """
    db = await get_db()

    conditions = ["processing_status = 'filtered'"]
    params: list = []
    since = utc_cutoff(days)
    if since is not None:
        conditions.append("created_at >= ?")
        params.append(since)
    where_clause = "WHERE " + " AND ".join(conditions)

    rows = await db.execute_fetchall(
        f"""SELECT event_id, timestamp, source_platform, source_raw_event_type,
                   subject_type, subject_id, subject_title, subject_url,
                   actor_name, actor_email, filter_rule, space_id, created_at
            FROM events
            {where_clause}
            ORDER BY created_at DESC""",
        params,
    )

    events = [dict(r) for r in rows]
    log.info("filtered_events_exported", count=len(events), days=days)
    return {
        "kind": "filtered_events",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "since": since,
        "count": len(events),
        "events": events,
    }


# ── dead event recovery ─────────────────────────────────────────────────

@router.get("/events/dead")
async def list_dead_events(limit: int = 25, offset: int = 0) -> dict:
    """List events that exhausted all retries and are permanently failed."""
    db = await get_db()

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) as total FROM events WHERE processing_status = 'dead'"
    )
    total = count_rows[0]["total"] if count_rows else 0

    rows = await db.execute_fetchall(
        """SELECT event_id, timestamp, source_platform, subject_type,
                  subject_title, subject_url, actor_name,
                  processing_attempts, manual_retries, last_error, created_at
           FROM events
           WHERE processing_status = 'dead'
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )

    return {
        "events": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/events/dead/retry", response_model=RetryDeadEventsResponse)
async def retry_dead_events(body: RetryDeadEventsRequest) -> RetryDeadEventsResponse:
    """Re-enqueue dead events for a full fresh retry cycle.

    Accepts either specific event_ids or all=true for bulk retry.
    Resets processing_attempts to 0 so the event gets 3 fresh automatic
    retries from the queue consumer.
    """
    db = await get_db()

    if body.all:
        cursor = await db.execute(
            """UPDATE events
               SET processing_status = 'queued',
                   processing_attempts = 0,
                   last_error = NULL,
                   next_retry_at = NULL,
                   manual_retries = manual_retries + 1
               WHERE processing_status = 'dead'"""
        )
    elif body.event_ids:
        placeholders = ",".join("?" for _ in body.event_ids)
        cursor = await db.execute(
            f"""UPDATE events
                SET processing_status = 'queued',
                    processing_attempts = 0,
                    last_error = NULL,
                    next_retry_at = NULL,
                    manual_retries = manual_retries + 1
                WHERE processing_status = 'dead'
                  AND event_id IN ({placeholders})""",
            tuple(body.event_ids),
        )
    else:
        return RetryDeadEventsResponse(retried=0)

    await db.commit()
    retried = cursor.rowcount

    if retried:
        log.info("dead_events_retried", count=retried, bulk=body.all)

    return RetryDeadEventsResponse(retried=retried)
