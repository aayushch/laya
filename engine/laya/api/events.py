"""POST /events — Accept, validate, store, and enqueue Laya Events."""

import json

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db
from laya.models.event import EventResponse, LayaEvent
from laya.pipeline.queue import enqueue_event

log = structlog.get_logger()
router = APIRouter()


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
