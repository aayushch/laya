# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Events API — Accept, validate, store, enqueue, and recover Laya Events."""

import json
from typing import Any, Optional

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from laya.api.audit_api import utc_cutoff
from laya.db.sqlite import get_db
from laya.db.timeutil import db_ts
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
            db_ts(event.timestamp),
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


# ── per-day event shape (Pulse timeline view) ───────────────────────────

def _day_window(date: str, tz: str | None) -> tuple[str, str, int]:
    """Return (utc_start, utc_end, offset_minutes) for a local calendar day.

    ``offset_minutes`` is the target zone's UTC offset at that day's local
    midnight; the bucket query shifts stored UTC timestamps by it so the
    density rail lines up with the clock-time axis the UI draws. A DST
    transition inside the day leaves post-transition events an hour off —
    accepted deliberately: the alternative is per-row zone conversion in
    Python, which means shipping every raw timestamp for a 10k-event day.
    Without a usable ``tz`` we fall back to the UTC clock, matching what
    /cards/grouped does when the client sends no timezone.
    """
    if tz:
        try:
            local_tz = ZoneInfo(tz)
            local_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=local_tz)
            local_end = local_start + timedelta(days=1)
            offset = local_start.utcoffset() or timedelta(0)
            return (
                local_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                local_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                int(offset.total_seconds() // 60),
            )
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            pass
    start = datetime.strptime(date, "%Y-%m-%d")
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        0,
    )


def _parse_cal_time(value: str) -> tuple[datetime | None, bool]:
    """Parse a calendar cal_start_time/cal_end_time into (dt, all_day).

    Google/Outlook send either a full offset-aware ISO timestamp
    ("2026-07-27T09:30:00-07:00") for timed events or a bare date
    ("2026-07-27") for all-day ones.
    """
    if not value or not isinstance(value, str):
        return None, False
    text = value.strip().replace("Z", "+00:00")
    if len(text) == 10:
        try:
            return datetime.strptime(text, "%Y-%m-%d"), True
        except ValueError:
            return None, False
    try:
        return datetime.fromisoformat(text), False
    except ValueError:
        return None, False


@router.get("/events/day")
async def get_day_events(
    date: str,
    space_id: str | None = None,
    tz: str | None = None,
    bucket_minutes: int = 30,
) -> dict:
    """Return the shape of one day's raw event stream, for the timeline view.

    The Pulse feed only ever loads *cards*, but the timeline needs the volume
    underneath them: the heat rail's density buckets, the per-platform source
    chip counts, and the calendar rail's meetings (whose real start/end live in
    the event's content_metadata, never on the card). One call per day change.

    Counts every stored event for the day — including filtered ones and events
    that never produced a card — because that unclassified mass is exactly what
    the heat rail exists to show.
    """
    db = await get_db()
    bucket = max(5, min(180, bucket_minutes))
    utc_start, utc_end, offset_minutes = _day_window(date, tz)

    conditions = ["timestamp >= ?", "timestamp < ?"]
    params: list[Any] = [utc_start, utc_end]
    space_ids = [s.strip() for s in (space_id or "").split(",") if s.strip()]
    if space_ids:
        placeholders = ",".join("?" for _ in space_ids)
        conditions.append(f"space_id IN ({placeholders})")
        params.extend(space_ids)
    where = " AND ".join(conditions)

    # Bucket in SQL (≤ platforms × buckets rows back) rather than streaming
    # every timestamp to Python.
    shift = f"{offset_minutes:+d} minutes"
    rows = await db.execute_fetchall(
        f"""SELECT source_platform AS platform,
                   ((CAST(strftime('%H', datetime(timestamp, ?)) AS INTEGER) * 60
                     + CAST(strftime('%M', datetime(timestamp, ?)) AS INTEGER)) / ?) * ? AS bucket,
                   COUNT(*) AS count
            FROM events
            WHERE {where}
            GROUP BY platform, bucket""",
        (shift, shift, bucket, bucket, *params),
    )

    platforms: dict[str, int] = {}
    bucket_counts: dict[int, int] = {}
    total = 0
    for r in rows:
        count = r["count"]
        total += count
        platform = (r["platform"] or "unknown").lower()
        platforms[platform] = platforms.get(platform, 0) + count
        start_minute = int(r["bucket"] or 0)
        bucket_counts[start_minute] = bucket_counts.get(start_minute, 0) + count

    # Meetings are keyed off cal_start_time, NOT the event timestamp — the
    # calendar workflows stamp events at ingest time, so a meeting synced at
    # 06:00 for a 14:00 slot would otherwise land in the wrong place entirely.
    # Candidates are narrowed by a date-substring match (the day plus its two
    # neighbours, so a meeting whose own UTC offset writes a different calendar
    # date still surfaces), then filtered exactly below.
    day = datetime.strptime(date, "%Y-%m-%d")
    like_dates = [
        (day + timedelta(days=delta)).strftime("%Y-%m-%d") for delta in (-1, 0, 1)
    ]
    meeting_conditions = [
        "subject_type = 'meeting'",
        "(" + " OR ".join("content_metadata LIKE ?" for _ in like_dates) + ")",
    ]
    meeting_params: list[Any] = [f"%{d}%" for d in like_dates]
    if space_ids:
        placeholders = ",".join("?" for _ in space_ids)
        meeting_conditions.append(f"space_id IN ({placeholders})")
        meeting_params.extend(space_ids)
    meeting_rows = await db.execute_fetchall(
        f"""SELECT event_id, source_platform, source_raw_event_type, subject_id,
                   subject_title, subject_url, content_metadata, timestamp
            FROM events
            WHERE {" AND ".join(meeting_conditions)}
            ORDER BY timestamp ASC""",
        tuple(meeting_params),
    )

    local_tz: timezone | ZoneInfo = timezone.utc
    if tz:
        try:
            local_tz = ZoneInfo(tz)
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            local_tz = timezone.utc

    # Later events for the same calendar entry supersede earlier ones — an
    # updated or cancelled meeting must render once, in its newest state.
    by_subject: dict[str, dict] = {}
    for r in meeting_rows:
        try:
            metadata = json.loads(r["content_metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        start_dt, all_day = _parse_cal_time(metadata.get("cal_start_time", ""))
        if start_dt is None:
            continue
        if all_day:
            local_date = start_dt.strftime("%Y-%m-%d")
        else:
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            local_date = start_dt.astimezone(local_tz).strftime("%Y-%m-%d")
        if local_date != date:
            continue
        end_dt, _ = _parse_cal_time(metadata.get("cal_end_time", ""))
        key = r["subject_id"] or r["event_id"]
        by_subject[key] = {
            "event_id": r["event_id"],
            "platform": (r["source_platform"] or "").lower(),
            "title": r["subject_title"] or "Untitled",
            "url": r["subject_url"],
            "start": metadata.get("cal_start_time"),
            "end": metadata.get("cal_end_time") if end_dt else None,
            "all_day": all_day,
            "cancelled": r["source_raw_event_type"] == "event_cancelled",
            "location": metadata.get("cal_location") or None,
            "attendee_count": len(metadata.get("cal_attendees") or []),
        }

    return {
        "date": date,
        "total": total,
        "bucket_minutes": bucket,
        "platforms": platforms,
        "buckets": [
            {"start_minute": m, "count": c} for m, c in sorted(bucket_counts.items())
        ],
        "meetings": sorted(by_subject.values(), key=lambda m: (m["all_day"], m["start"] or "")),
    }


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
