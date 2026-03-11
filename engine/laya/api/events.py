"""POST /events — Accept, validate, store, and process Laya Events."""

import asyncio
import json
import uuid

import structlog
from fastapi import APIRouter

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.models.classification import RouterOutput
from laya.models.event import EventResponse, LayaEvent
from laya.pipeline.emit import run_emit
from laya.pipeline.ingest import run_ingest
from laya.pipeline.router import run_router
from laya.pipeline.rules import run_rules
from laya.pipeline.space_resolution import resolve_space
from laya.pipeline.stager import run_stager
from laya.pipeline.workers import run_workers

log = structlog.get_logger()
router = APIRouter()


@router.post("/events", response_model=EventResponse, status_code=202)
async def receive_event(event: LayaEvent) -> EventResponse:
    """Receive a normalized event from n8n, store it, and process through pipeline."""
    db = await get_db()

    # Store in SQLite — ignore duplicates (n8n may re-deliver on retry)
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO events (
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

    if cursor.rowcount == 0:
        # Event already exists — check if it completed the pipeline
        db2 = await get_db()
        async with db2.execute(
            "SELECT processed FROM events WHERE event_id = ?", (event.event_id,)
        ) as cur:
            row = await cur.fetchone()
        if row and row["processed"]:
            log.info("event_duplicate_skipped", event_id=event.event_id)
            return EventResponse(event_id=event.event_id)
        log.info("event_reprocessing_stuck", event_id=event.event_id)

    log.info(
        "event_stored",
        event_id=event.event_id,
        platform=event.source.platform,
        subject=event.subject.title,
    )

    # INGEST: resolve actor relationship from team.json
    actor_relationship = await run_ingest(event)

    # SPACE RESOLUTION: map source workflow to space
    space_id = await resolve_space(
        event.event_id, event.source.connection_id, event.source.platform
    )

    # RULES ENGINE: check filter rules
    filtered, filter_rule = await run_rules(event)

    # Only process and broadcast if NOT filtered
    if not filtered:
        # ROUTER: classify event via LLM
        router_output = None
        try:
            router_output = await run_router(event, actor_relationship, space_id=space_id)
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

        # Dispatch background pipeline based on research needs
        if router_output and router_output.requires_research:
            asyncio.create_task(
                _run_workers_background(event, router_output, space_id=space_id),
                name=f"workers_{event.event_id}",
            )
        elif router_output:
            asyncio.create_task(
                _run_simple_card_background(event, router_output, space_id=space_id),
                name=f"simple_card_{event.event_id}",
            )

    return EventResponse(event_id=event.event_id)


async def _run_workers_background(
    event: LayaEvent, router_output: RouterOutput, space_id: str | None = None
) -> None:
    """Run workers → stager → emit in the background."""
    # Pre-generate card_id so it exists in action_cards before workers run.
    # The engineer worker creates workspace_sessions with a FK to action_cards,
    # so the card row must exist first.
    card_id: str | None = None
    try:
        card_id = f"card_{uuid.uuid4().hex[:12]}"
        entity_id = f"{event.source.platform}:{event.subject.type}:{event.subject.id}"
        db = await get_db()
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category, header, summary,
                status, privacy_tier, has_workspace, entity_id, space_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id,
                event.event_id,
                router_output.priority.value,
                router_output.persona.value,
                router_output.category.value,
                event.subject.title,
                "Researching…",
                "agent_running",
                2,
                True,
                entity_id,
                space_id,
            ),
        )
        await db.commit()

        # Broadcast the provisional card so the UI shows agent_running immediately.
        # Without this the card is invisible until the full pipeline finishes.
        await manager.broadcast(
            {
                "type": "card_created",
                "card_id": card_id,
                "payload": {
                    "header": event.subject.title,
                    "summary": "Researching…",
                    "priority": router_output.priority.value,
                    "persona": router_output.persona.value,
                    "category": router_output.category.value,
                    "status": "agent_running",
                    "has_workspace": True,
                    "privacy_tier": 2,
                },
            }
        )

        results = await run_workers(event, router_output, card_id=card_id, space_id=space_id)
        log.info(
            "background_workers_complete",
            event_id=event.event_id,
            worker_count=len(results),
            errors=[r.error for r in results if r.error],
        )

        # Stager: synthesize findings into card
        stager_output = await run_stager(event, router_output, results, space_id=space_id)

        # Emit: update the pre-created card with final stager data
        card_id = await run_emit(event, router_output, stager_output, results, card_id=card_id, space_id=space_id)
        log.info("card_emitted", event_id=event.event_id, card_id=card_id)

    except Exception as e:
        log.error("background_pipeline_failed", event_id=event.event_id, error=str(e))
        # Mark the provisional card as failed so it doesn't stay stuck as agent_running
        if card_id:
            try:
                db = await get_db()
                await db.execute(
                    "UPDATE action_cards SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE card_id=?",
                    (card_id,),
                )
                await db.commit()
            except Exception:
                pass


async def _run_simple_card_background(
    event: LayaEvent, router_output: RouterOutput, space_id: str | None = None
) -> None:
    """Run stager → emit for simple events (no workers needed)."""
    try:
        stager_output = await run_stager(event, router_output, worker_results=None, space_id=space_id)
        card_id = await run_emit(event, router_output, stager_output, space_id=space_id)
        log.info("simple_card_emitted", event_id=event.event_id, card_id=card_id)
    except Exception as e:
        log.error("simple_card_failed", event_id=event.event_id, error=str(e))
