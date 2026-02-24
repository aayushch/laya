"""POST /events — Accept, validate, store, and process Laya Events."""

import asyncio
import json

import structlog
from fastapi import APIRouter

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.models.classification import RouterOutput
from laya.models.event import EventResponse, LayaEvent
from laya.pipeline.ingest import run_ingest
from laya.pipeline.router import run_router
from laya.pipeline.rules import run_rules
from laya.pipeline.workers import run_workers

log = structlog.get_logger()
router = APIRouter()


@router.post("/events", response_model=EventResponse, status_code=202)
async def receive_event(event: LayaEvent) -> EventResponse:
    """Receive a normalized event from n8n, store it, and process through pipeline."""
    db = await get_db()

    # Store in SQLite
    await db.execute(
        """
        INSERT INTO events (
            event_id, timestamp, source_platform, source_connection_id,
            source_raw_event_type, actor_name, actor_email, actor_handle,
            subject_type, subject_id, subject_title, subject_url,
            content_body, content_metadata, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    log.info(
        "event_stored",
        event_id=event.event_id,
        platform=event.source.platform,
        subject=event.subject.title,
    )

    # INGEST: resolve actor relationship from team.json
    actor_relationship = await run_ingest(event)

    # RULES ENGINE: check filter rules
    filtered, filter_rule = await run_rules(event)

    # Only process and broadcast if NOT filtered
    if not filtered:
        # ROUTER: classify event via LLM
        router_output = None
        try:
            router_output = await run_router(event, actor_relationship)
        except Exception as e:
            log.error("router_failed", event_id=event.event_id, error=str(e))

        # Build broadcast payload
        broadcast_payload: dict = {
            "platform": event.source.platform,
            "subject_type": event.subject.type,
            "subject_title": event.subject.title,
            "actor": event.actor.name,
            "actor_relationship": actor_relationship,
        }
        if router_output:
            broadcast_payload.update(
                {
                    "category": router_output.category.value,
                    "persona": router_output.persona.value,
                    "priority": router_output.priority.value,
                    "requires_research": router_output.requires_research,
                }
            )

        await manager.broadcast(
            {
                "type": "event_classified",
                "event_id": event.event_id,
                "payload": broadcast_payload,
            }
        )

        # Kick off workers as background task if research is required
        if router_output and router_output.requires_research:
            asyncio.create_task(
                _run_workers_background(event, router_output),
                name=f"workers_{event.event_id}",
            )

    return EventResponse(event_id=event.event_id)


async def _run_workers_background(event: LayaEvent, router_output: RouterOutput) -> None:
    """Run workers in the background after router classification."""
    try:
        results = await run_workers(event, router_output)
        log.info(
            "background_workers_complete",
            event_id=event.event_id,
            worker_count=len(results),
            errors=[r.error for r in results if r.error],
        )
    except Exception as e:
        log.error("background_workers_failed", event_id=event.event_id, error=str(e))
