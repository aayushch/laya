"""Database-backed event queue with concurrency control and retry logic.

Events flow: queued → processing → completed | failed
Failed events retry with exponential backoff up to max_attempts.
A background consumer loop polls the queue and dispatches work
through a concurrency-limited semaphore.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent

log = structlog.get_logger()

# Module-level state
_consumer_task: asyncio.Task | None = None
_semaphore: asyncio.Semaphore | None = None
_shutdown_event: asyncio.Event = asyncio.Event()

# ── settings helpers ──────────────────────────────────────────────────────

def _get_pipeline_settings() -> dict:
    """Read pipeline config from settings.json with sane defaults."""
    settings = load_settings()
    pipeline = settings.get("pipeline", {})
    return {
        "max_concurrent_events": int(pipeline.get("max_concurrent_events", 5)),
        "max_retry_attempts": int(pipeline.get("max_retry_attempts", 5)),
        "queue_poll_interval": float(pipeline.get("queue_poll_interval", 2.0)),
        "model_timeout": float(pipeline.get("model_timeout", 120)),
    }


def get_model_timeout() -> float:
    """Return the configured model timeout (seconds). Used by llm/client.py."""
    return _get_pipeline_settings()["model_timeout"]


def get_llm_retries() -> int:
    """Return configured per-call LLM retry attempts. Used by llm/client.py."""
    cfg = _get_pipeline_settings()
    return int(cfg.get("llm_retries", 3))


def _get_semaphore() -> asyncio.Semaphore:
    """Return (and lazily create) the global concurrency semaphore."""
    global _semaphore
    cfg = _get_pipeline_settings()
    limit = cfg["max_concurrent_events"]
    if _semaphore is None or _semaphore._value != limit:  # noqa: SLF001
        _semaphore = asyncio.Semaphore(limit)
    return _semaphore


# ── queue operations ─────────────────────────────────────────────────────

async def enqueue_event(event_id: str) -> None:
    """Mark an event as queued for processing."""
    db = await get_db()
    await db.execute(
        "UPDATE events SET processing_status = 'queued', next_retry_at = NULL WHERE event_id = ?",
        (event_id,),
    )
    await db.commit()


async def _claim_event(event_id: str) -> bool:
    """Atomically claim an event for processing. Returns True if claimed."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """UPDATE events
           SET processing_status = 'processing',
               processing_attempts = processing_attempts + 1,
               processing_started_at = ?
           WHERE event_id = ? AND processing_status IN ('queued', 'retrying')""",
        (now, event_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def _mark_completed(event_id: str) -> None:
    """Mark an event as fully processed."""
    db = await get_db()
    await db.execute(
        """UPDATE events
           SET processing_status = 'completed', processed = 1,
               last_error = NULL, next_retry_at = NULL
           WHERE event_id = ?""",
        (event_id,),
    )
    await db.commit()


async def _mark_filtered(event_id: str) -> None:
    """Mark an event as filtered (terminal, no card produced)."""
    db = await get_db()
    await db.execute(
        """UPDATE events
           SET processing_status = 'filtered', processed = 1,
               last_error = NULL, next_retry_at = NULL
           WHERE event_id = ?""",
        (event_id,),
    )
    await db.commit()


async def _mark_failed(event_id: str, error: str) -> None:
    """Mark an event as failed and schedule retry if under max attempts."""
    cfg = _get_pipeline_settings()
    max_attempts = cfg["max_retry_attempts"]

    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT processing_attempts FROM events WHERE event_id = ?", (event_id,)
    )
    attempts = row[0]["processing_attempts"] if row else 0

    if attempts < max_attempts:
        # Exponential backoff: 2^attempt seconds, capped at 5 minutes
        delay = min(2 ** attempts, 300)
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay)
        await db.execute(
            """UPDATE events
               SET processing_status = 'retrying',
                   last_error = ?,
                   next_retry_at = ?
               WHERE event_id = ?""",
            (error, next_retry.isoformat(), event_id),
        )
        log.info(
            "event_scheduled_retry",
            event_id=event_id,
            attempt=attempts,
            next_retry_in=f"{delay}s",
        )
    else:
        await db.execute(
            """UPDATE events
               SET processing_status = 'dead',
                   last_error = ?,
                   next_retry_at = NULL
               WHERE event_id = ?""",
            (error, event_id),
        )
        log.error(
            "event_permanently_failed",
            event_id=event_id,
            attempts=attempts,
            error=error,
        )
    await db.commit()


# ── event processing ─────────────────────────────────────────────────────

async def _load_event(event_id: str) -> LayaEvent | None:
    """Reconstruct a LayaEvent from the stored raw_json."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT raw_json FROM events WHERE event_id = ?", (event_id,)
    )
    if not rows:
        return None
    try:
        return LayaEvent.model_validate_json(rows[0]["raw_json"])
    except Exception as e:
        log.error("event_load_failed", event_id=event_id, error=str(e))
        return None


async def process_event(event_id: str) -> None:
    """Run the full pipeline for a single event (with claim/complete/fail)."""
    from laya.api.websocket import manager
    from laya.models.classification import RouterOutput
    from laya.pipeline.ingest import run_ingest
    from laya.pipeline.router import run_router
    from laya.pipeline.rules import run_rules
    from laya.pipeline.space_resolution import resolve_space
    from laya.pipeline.stager import run_stager
    from laya.pipeline.emit import run_emit
    from laya.pipeline.workers import run_workers

    if not await _claim_event(event_id):
        return  # already being processed

    event = await _load_event(event_id)
    if not event:
        await _mark_failed(event_id, "Could not load event from raw_json")
        return

    try:
        # INGEST
        actor_relationship = await run_ingest(event)

        # SPACE RESOLUTION
        space_id = await resolve_space(
            event.event_id, event.source.connection_id, event.source.platform
        )

        # RULES ENGINE
        filtered, filter_rule = await run_rules(event)
        if filtered:
            await _mark_filtered(event_id)
            await manager.broadcast(
                {
                    "type": "event_classified",
                    "event_id": event.event_id,
                    "payload": {
                        "filtered": True,
                        "filter_rule": filter_rule,
                    },
                }
            )
            return

        # ROUTER
        router_output: RouterOutput | None = None
        try:
            router_output = await run_router(event, actor_relationship, space_id=space_id)
        except Exception as e:
            log.error("router_failed", event_id=event.event_id, error=str(e))
            raise  # let the queue retry

        # Broadcast classification
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

        # WORKERS → STAGER → EMIT (inline, not background fire-and-forget)
        if router_output and router_output.requires_research:
            await _run_workers_pipeline(event, router_output, space_id)
        elif router_output:
            await _run_simple_pipeline(event, router_output, space_id)

        await _mark_completed(event_id)

    except BaseException as e:
        # BaseException catches both Exception and asyncio.CancelledError,
        # ensuring cancelled tasks don't orphan events in 'processing' state.
        error_msg = f"{type(e).__name__}: {e}"
        log.error("event_processing_failed", event_id=event_id, error=error_msg)
        try:
            await _mark_failed(event_id, error_msg)
        except Exception:
            pass  # DB may be unavailable during shutdown
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise


async def _run_workers_pipeline(
    event: LayaEvent, router_output, space_id: str | None
) -> None:
    """Workers → Stager → Emit with pre-created card."""
    import uuid as _uuid

    from laya.api.websocket import manager
    from laya.pipeline.emit import run_emit
    from laya.pipeline.stager import run_stager
    from laya.pipeline.workers import run_workers

    card_id = f"card_{_uuid.uuid4().hex[:12]}"
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
            "Researching\u2026",
            "pending",
            2,
            False,
            entity_id,
            space_id,
        ),
    )
    await db.commit()

    await manager.broadcast(
        {
            "type": "card_created",
            "card_id": card_id,
            "payload": {
                "header": event.subject.title,
                "summary": "Researching\u2026",
                "priority": router_output.priority.value,
                "persona": router_output.persona.value,
                "category": router_output.category.value,
                "status": "pending",
                "has_workspace": False,
                "privacy_tier": 2,
            },
        }
    )

    try:
        results = await run_workers(event, router_output, card_id=card_id, space_id=space_id)
        stager_output = await run_stager(event, router_output, results, space_id=space_id)
        await run_emit(event, router_output, stager_output, results, card_id=card_id, space_id=space_id)
    except Exception as e:
        # Mark the provisional card as failed
        try:
            db = await get_db()
            await db.execute(
                "UPDATE action_cards SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE card_id=?",
                (card_id,),
            )
            await db.commit()
        except Exception:
            pass
        raise


async def _run_simple_pipeline(
    event: LayaEvent, router_output, space_id: str | None
) -> None:
    """Stager → Emit for simple events."""
    from laya.pipeline.emit import run_emit
    from laya.pipeline.stager import run_stager

    stager_output = await run_stager(event, router_output, worker_results=None, space_id=space_id)
    await run_emit(event, router_output, stager_output, space_id=space_id)


# ── recovery & watchdog ───────────────────────────────────────────────────

async def recover_stalled_events() -> int:
    """Reset events stuck in 'processing' back to 'retrying'.

    Called on startup to recover events orphaned by a crash or forced
    shutdown. Any event in 'processing' at startup was mid-flight when
    the engine stopped — no consumer is running yet, so they're stale.
    """
    db = await get_db()
    cursor = await db.execute(
        """UPDATE events
           SET processing_status = 'retrying',
               last_error = 'recovered: engine restarted while processing',
               next_retry_at = ?
           WHERE processing_status = 'processing'""",
        (datetime.now(timezone.utc).isoformat(),),
    )
    await db.commit()
    count = cursor.rowcount
    if count:
        log.warning("stalled_events_recovered", count=count)
    return count


async def _reap_stale_events() -> int:
    """Find events stuck in 'processing' longer than 2× model_timeout.

    This catches cases where a task silently hangs while the engine is
    still running (e.g., LM Studio killed mid-request and httpx doesn't
    respect the timeout).
    """
    cfg = _get_pipeline_settings()
    stale_threshold = cfg["model_timeout"] * 2
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_threshold)
    ).isoformat()

    db = await get_db()
    cursor = await db.execute(
        """UPDATE events
           SET processing_status = 'retrying',
               last_error = 'recovered: stale processing (exceeded timeout)',
               next_retry_at = ?
           WHERE processing_status = 'processing'
             AND processing_started_at IS NOT NULL
             AND processing_started_at < ?""",
        (datetime.now(timezone.utc).isoformat(), cutoff),
    )
    await db.commit()
    count = cursor.rowcount
    if count:
        log.warning("stale_events_reaped", count=count, threshold_seconds=stale_threshold)
    return count


# ── background consumer ──────────────────────────────────────────────────

_STALE_CHECK_INTERVAL = 30  # seconds between stale event checks

async def _fetch_ready_events(limit: int) -> list[str]:
    """Fetch event_ids that are ready for processing."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    rows = await db.execute_fetchall(
        """SELECT event_id FROM events
           WHERE processing_status = 'queued'
              OR (processing_status = 'retrying' AND next_retry_at <= ?)
           ORDER BY
               CASE WHEN processing_status = 'queued' THEN 0 ELSE 1 END,
               created_at ASC
           LIMIT ?""",
        (now, limit),
    )
    return [r["event_id"] for r in rows]


async def _consumer_loop() -> None:
    """Background loop that polls the queue and dispatches event processing."""
    log.info("queue_consumer_started")

    last_stale_check = 0.0

    while not _shutdown_event.is_set():
        try:
            cfg = _get_pipeline_settings()
            poll_interval = cfg["queue_poll_interval"]

            # Periodically reap stale events
            import time
            now_mono = time.monotonic()
            if now_mono - last_stale_check >= _STALE_CHECK_INTERVAL:
                await _reap_stale_events()
                last_stale_check = now_mono

            event_ids = await _fetch_ready_events(limit=cfg["max_concurrent_events"])
            if not event_ids:
                try:
                    await asyncio.wait_for(
                        _shutdown_event.wait(), timeout=poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            sem = _get_semaphore()
            tasks = []
            for eid in event_ids:
                task = asyncio.create_task(
                    _process_with_semaphore(sem, eid),
                    name=f"queue_{eid}",
                )
                tasks.append(task)

            # Wait for this batch (or until shutdown)
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.ALL_COMPLETED
            )

        except Exception as e:
            log.error("queue_consumer_error", error=str(e))
            await asyncio.sleep(5)

    log.info("queue_consumer_stopped")


async def _process_with_semaphore(sem: asyncio.Semaphore, event_id: str) -> None:
    """Acquire semaphore then process one event."""
    async with sem:
        await process_event(event_id)


# ── lifecycle ─────────────────────────────────────────────────────────────

def start_consumer() -> None:
    """Start the background queue consumer. Call during app startup."""
    global _consumer_task
    _shutdown_event.clear()
    _consumer_task = asyncio.create_task(_consumer_loop(), name="queue_consumer")


async def stop_consumer() -> None:
    """Gracefully stop the queue consumer. Call during app shutdown."""
    global _consumer_task
    _shutdown_event.set()
    if _consumer_task and not _consumer_task.done():
        try:
            await asyncio.wait_for(_consumer_task, timeout=10)
        except asyncio.TimeoutError:
            _consumer_task.cancel()
    _consumer_task = None
