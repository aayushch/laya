# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Database-backed event queue with concurrency control and retry logic.

Events flow: queued → processing → completed | failed
Failed events retry with exponential backoff up to max_attempts.
A background consumer loop polls the queue and dispatches work
through a concurrency-limited semaphore.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import structlog

from laya.config import get_debounce_config, load_settings
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent

log = structlog.get_logger()

# Module-level state
_consumer_task: asyncio.Task | None = None
_semaphore: asyncio.Semaphore | None = None
_shutdown_event: asyncio.Event = asyncio.Event()
# Pre-computed router outputs from batch routing (event_id → RouterOutput).
# Populated by _batch_route_events, consumed by process_event.
_batch_router_cache: dict[str, object] = {}
# Batch-routing circuit-breaker. Batch routing balloons the router prompt to ~10×;
# on a local reasoning model that overflows the loaded context window and the call
# truncates/errors, producing no usable classifications. When that happens we disable
# batch routing for a cooldown so a large backlog drain stops re-paying the doomed cost
# every poll cycle — events fall back to individual routing, which has a far smaller
# prompt. Monotonic deadline; 0 means enabled.
_batch_routing_disabled_until: float = 0.0
_BATCH_BREAKER_COOLDOWN = 300  # seconds
# Track in-flight processing tasks so we can cancel them on shutdown,
# aborting pending LLM HTTP requests instead of blocking until they complete.
_inflight_tasks: set[asyncio.Task] = set()

# ── settings helpers ──────────────────────────────────────────────────────

def _get_pipeline_settings() -> dict:
    """Read pipeline config from settings.json with sane defaults."""
    settings = load_settings()
    pipeline = settings.get("pipeline", {})
    return {
        "max_concurrent_events": int(pipeline.get("max_concurrent_events", 4)),
        "max_retry_attempts": int(pipeline.get("max_retry_attempts", 3)),
        "queue_poll_interval": float(pipeline.get("queue_poll_interval", 2.0)),
        "model_timeout": float(pipeline.get("model_timeout", 480)),
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

    became_dead = False
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
        became_dead = True
    await db.commit()

    # Push the Audit/Settings red dot live when an event becomes permanently
    # failed. Without this the indicator would only update on the next app
    # startup (or when the user opens the Audit tab) since these tables are not
    # polled. Best-effort: a WS hiccup must never break the pipeline.
    if became_dead:
        try:
            from laya.api.websocket import manager
            from laya.api.audit_api import compute_failure_counts

            counts = await compute_failure_counts(db)
            await manager.broadcast(
                {"type": "audit_failure", "payload": {**counts, "kind": "dead_event"}}
            )
        except Exception as e:
            log.warning("audit_failure_broadcast_failed", event_id=event_id, error=str(e))


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
        # Load user identity (the person running Laya) for prompt personalization
        from laya.config import get_self_user
        user_identity = get_self_user()

        # INGEST
        actor_relationship, participant_roles = await run_ingest(event)

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

        # ROUTER — check batch cache first (populated by _batch_route_events)
        router_output: RouterOutput | None = _batch_router_cache.pop(event_id, None)
        if router_output:
            log.debug("router_from_batch_cache", event_id=event.event_id)
        else:
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

        # Check for existing card from a prior failed attempt (retry scenario).
        # Reuse the same card_id so emit UPDATEs instead of INSERTing a duplicate.
        existing_card_id = None
        db = await get_db()
        _existing_rows = await db.execute_fetchall(
            "SELECT card_id FROM action_cards WHERE event_id = ? LIMIT 1",
            (event_id,),
        )
        if _existing_rows:
            existing_card_id = _existing_rows[0]["card_id"]

        # WORKERS → STAGER → EMIT (inline, not background fire-and-forget)
        if router_output and router_output.requires_research:
            await _run_workers_pipeline(event, router_output, space_id, user_identity=user_identity, actor_relationship=actor_relationship, participant_roles=participant_roles, existing_card_id=existing_card_id)
        elif router_output:
            await _run_simple_pipeline(event, router_output, space_id, user_identity=user_identity, actor_relationship=actor_relationship, participant_roles=participant_roles, existing_card_id=existing_card_id)

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
    event: LayaEvent, router_output, space_id: str | None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
    existing_card_id: str | None = None,
) -> None:
    """Workers → Stager → Emit with pre-created card."""
    import uuid as _uuid

    from laya.api.websocket import manager
    from laya.pipeline.emit import run_emit
    from laya.pipeline.stager import run_stager
    from laya.pipeline.workers import run_workers

    if existing_card_id:
        # Retry: reuse the existing card, reset it to provisional state
        card_id = existing_card_id
        db = await get_db()
        await db.execute(
            """UPDATE action_cards SET
               status='pending', header=?, summary='Researching\u2026',
               failed_stage=NULL, updated_at=CURRENT_TIMESTAMP
               WHERE card_id=?""",
            (event.subject.title, card_id),
        )
        await db.commit()
        await manager.broadcast(
            {
                "type": "card_updated",
                "card_id": card_id,
                "payload": {
                    "header": event.subject.title,
                    "summary": "Researching\u2026",
                    "status": "pending",
                },
            }
        )
    else:
        card_id = f"card_{_uuid.uuid4().hex[:12]}"
        entity_id = event.entity_id  # canonical key; see LayaEvent.entity_id
        db = await get_db()
        # Stamp created_at/group_active_at with the EVENT's time, not wall-clock now \u2014
        # the worker path's emit UPDATE never rewrites created_at, so this is the only
        # place a worker card's time is set. Without it, late-ingested cards read "just now".
        from laya.pipeline.emit import _event_ts
        ev_ts = _event_ts(event)
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category, header, summary,
                status, privacy_tier, has_workspace, entity_id, space_id,
                created_at, group_active_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ev_ts,
                ev_ts,
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
        results = await run_workers(event, router_output, card_id=card_id, space_id=space_id, user_identity=user_identity, actor_relationship=actor_relationship, participant_roles=participant_roles)
        stager_output = await run_stager(event, router_output, results, space_id=space_id, user_identity=user_identity, actor_relationship=actor_relationship, participant_roles=participant_roles)
        await run_emit(event, router_output, stager_output, results, card_id=card_id, space_id=space_id)
    except Exception as e:
        # Mark the provisional card as failed
        try:
            db = await get_db()
            await db.execute(
                "UPDATE action_cards SET status='failed', failed_stage='pipeline', updated_at=CURRENT_TIMESTAMP WHERE card_id=?",
                (card_id,),
            )
            await db.commit()
        except Exception:
            pass
        raise


async def _run_simple_pipeline(
    event: LayaEvent, router_output, space_id: str | None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
    existing_card_id: str | None = None,
) -> None:
    """Stager → Emit for simple events."""
    from laya.pipeline.emit import run_emit
    from laya.pipeline.stager import run_stager

    stager_output = await run_stager(event, router_output, worker_results=None, space_id=space_id, user_identity=user_identity, actor_relationship=actor_relationship, participant_roles=participant_roles)
    await run_emit(event, router_output, stager_output, space_id=space_id, card_id=existing_card_id)


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
        from laya.llm.client import log_to_audit
        await log_to_audit(
            event_id=None, card_id=None, step="recovery",
            model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
            success=True,
            metadata={"action": "stalled_events_reset", "count": count},
        )
    return count


async def recover_stalled_cards() -> int:
    """Reset cards stuck in transient states after a crash or forced shutdown.

    Called on startup alongside recover_stalled_events(). Handles:
    - 'pending' cards whose event will be retried → delete (fresh card will
      be created when the event is reprocessed).
    - 'pending' cards whose event is terminal (dead/completed) → mark failed
      since no retry is coming.
    - 'agent_running' cards → revert to ready so user can
      re-invoke the agent (agent session is dead after restart).
    - 'executing' cards → mark failed since we can't know if the external
      action completed.
    """
    db = await get_db()
    total = 0

    # -- pending cards with retryable events: delete silently --
    cursor = await db.execute(
        """DELETE FROM action_cards
           WHERE status = 'pending'
             AND event_id IN (
                 SELECT event_id FROM events
                 WHERE processing_status IN ('queued', 'retrying', 'processing')
             )"""
    )
    deleted = cursor.rowcount
    total += deleted
    if deleted:
        log.warning("stalled_pending_cards_deleted", count=deleted,
                    reason="event will be retried, fresh card will be created")

    # -- pending cards with terminal events: mark failed --
    cursor = await db.execute(
        """UPDATE action_cards
           SET status = 'failed',
               failed_stage = 'pipeline',
               updated_at = CURRENT_TIMESTAMP
           WHERE status = 'pending'
             AND event_id IN (
                 SELECT event_id FROM events
                 WHERE processing_status IN ('dead', 'completed')
             )"""
    )
    failed_pending = cursor.rowcount
    total += failed_pending
    if failed_pending:
        log.warning("stalled_pending_cards_failed", count=failed_pending,
                    reason="event is terminal, no retry coming")

    # -- agent_running → ready --
    cursor = await db.execute(
        """UPDATE action_cards
           SET status = 'ready',
               previous_status = 'agent_running',
               updated_at = CURRENT_TIMESTAMP
           WHERE status = 'agent_running'"""
    )
    agent_reset = cursor.rowcount
    total += agent_reset
    if agent_reset:
        log.warning("stalled_agent_cards_reset", count=agent_reset,
                    reason="agent session dead after restart, reverted to ready")

    # -- executing → failed --
    cursor = await db.execute(
        """UPDATE action_cards
           SET status = 'failed',
               failed_stage = 'action_execution',
               updated_at = CURRENT_TIMESTAMP
           WHERE status = 'executing'"""
    )
    exec_failed = cursor.rowcount
    total += exec_failed
    if exec_failed:
        log.warning("stalled_executing_cards_failed", count=exec_failed,
                    reason="action execution interrupted, cannot verify completion")

    await db.commit()

    if total:
        log.warning("stalled_cards_recovered", total=total,
                    deleted_pending=deleted, failed_pending=failed_pending,
                    agent_reset=agent_reset, exec_failed=exec_failed)
        from laya.llm.client import log_to_audit
        await log_to_audit(
            event_id=None, card_id=None, step="recovery",
            model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
            success=True,
            metadata={
                "action": "stalled_cards_recovered",
                "deleted_pending": deleted,
                "failed_pending": failed_pending,
                "agent_reset": agent_reset,
                "exec_failed": exec_failed,
            },
        )
    return total


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
        from laya.llm.client import log_to_audit
        await log_to_audit(
            event_id=None, card_id=None, step="recovery",
            model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
            success=True,
            metadata={"action": "stale_events_reaped", "count": count,
                      "threshold_seconds": stale_threshold},
        )
    return count


# ── batch routing ────────────────────────────────────────────────────────


def _trip_batch_breaker(reason: str, **fields) -> None:
    """Disable batch routing for a cooldown after a failed/partial batch route."""
    global _batch_routing_disabled_until
    _batch_routing_disabled_until = time.monotonic() + _BATCH_BREAKER_COOLDOWN
    log.warning(
        "batch_routing_disabled", reason=reason, cooldown_s=_BATCH_BREAKER_COOLDOWN, **fields
    )


def _batch_routing_allowed() -> bool:
    """True if the batch-route circuit-breaker is not currently tripped."""
    return time.monotonic() >= _batch_routing_disabled_until


def _router_is_local_provider() -> bool:
    """True if the configured router model should NOT be batch-routed.

    Batch routing balloons the prompt to ~10×. For self-hosted/custom providers
    (LMStudio, Ollama) that reliably overflows the loaded context window; for the agent
    inference backend each call spawns a process and spends the user's plan quota, so the
    big batch prompt is wasteful and slow. Both route individually instead."""
    try:
        from laya.llm.agent_backend import is_agent_model
        from laya.llm.client import _get_model_for_role, _resolve_custom_provider

        router_model = _get_model_for_role("router")
        if is_agent_model(router_model):
            return True
        return _resolve_custom_provider(router_model) is not None
    except Exception:
        return False


async def _batch_route_events(event_ids: list[str]) -> None:
    """Pre-classify multiple events in one LLM call (populates _batch_router_cache).

    Best-effort: failures are logged and events fall back to individual routing.
    Events are NOT claimed here — they are only loaded for classification.
    """
    from laya.pipeline.ingest import run_ingest
    from laya.pipeline.router import run_batch_router
    from laya.pipeline.rules import run_rules
    from laya.pipeline.space_resolution import resolve_space

    events_data = []
    for eid in event_ids:
        event = await _load_event(eid)
        if not event:
            continue
        try:
            actor_relationship, _ = await run_ingest(event)
            space_id = await resolve_space(
                eid, event.source.connection_id, event.source.platform
            )
            filtered, _ = await run_rules(event)
            if filtered:
                continue
            events_data.append({
                "event_id": eid,
                "event": event,
                "actor_relationship": actor_relationship,
                "space_id": space_id,
            })
        except Exception as e:
            log.debug("batch_route_prep_skipped", event_id=eid, error=str(e))

    if len(events_data) < 2:
        return  # Not enough events for batching to save anything

    try:
        results = await run_batch_router(events_data)
        _batch_router_cache.update(results)
        log.info("batch_route_cached", count=len(results))
        # A partial/empty result means the batch LLM call truncated or returned
        # unparseable JSON for some events — trip the breaker so we stop re-attempting
        # doomed batch routes during a large drain.
        if len(results) < len(events_data):
            _trip_batch_breaker(
                "partial_batch_result", got=len(results), expected=len(events_data)
            )
    except Exception as e:
        log.warning("batch_route_failed", error=str(e))
        _trip_batch_breaker("batch_route_error", error=str(e))


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
    """Background loop that polls the queue and dispatches event processing.

    When event_batch_window_seconds > 0, collects events for a short window
    before processing to enable batch-routing (multiple events classified in
    one LLM call). When set to 0, events process immediately as before.
    """
    log.info("queue_consumer_started")

    last_stale_check = 0.0

    while not _shutdown_event.is_set():
        try:
            cfg = _get_pipeline_settings()
            debounce_cfg = get_debounce_config()
            poll_interval = cfg["queue_poll_interval"]
            batch_window = debounce_cfg.get("event_batch_window_seconds", 3)
            batch_max = debounce_cfg.get("event_batch_max_size", 10)

            # Periodically reap stale events
            import time
            now_mono = time.monotonic()
            if now_mono - last_stale_check >= _STALE_CHECK_INTERVAL:
                await _reap_stale_events()
                last_stale_check = now_mono

            fetch_limit = min(batch_max, cfg["max_concurrent_events"]) if batch_window > 0 else cfg["max_concurrent_events"]
            event_ids = await _fetch_ready_events(limit=fetch_limit)
            if not event_ids:
                try:
                    await asyncio.wait_for(
                        _shutdown_event.wait(), timeout=poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # Batch collection: wait briefly for more events to arrive
            if batch_window > 0 and len(event_ids) < batch_max:
                try:
                    await asyncio.wait_for(
                        _shutdown_event.wait(),
                        timeout=min(batch_window, poll_interval),
                    )
                    break  # Shutdown requested during batch window
                except asyncio.TimeoutError:
                    pass
                # Fetch any new arrivals that queued during the window
                extra_ids = await _fetch_ready_events(limit=batch_max)
                for eid in extra_ids:
                    if eid not in event_ids:
                        event_ids.append(eid)
                        if len(event_ids) >= batch_max:
                            break

            # Attempt batch routing only when it's worthwhile and safe: multiple
            # events ready, batching enabled, the circuit-breaker isn't tripped, and
            # the router isn't a local provider (batching overflows local context
            # windows — route those individually).
            if (
                len(event_ids) > 1
                and batch_window > 0
                and _batch_routing_allowed()
                and not _router_is_local_provider()
            ):
                await _batch_route_events(event_ids)

            sem = _get_semaphore()
            tasks = []
            for eid in event_ids:
                task = asyncio.create_task(
                    _process_with_semaphore(sem, eid),
                    name=f"queue_{eid}",
                )
                tasks.append(task)
                _inflight_tasks.add(task)
                task.add_done_callback(_inflight_tasks.discard)

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
    """Gracefully stop the queue consumer. Call during app shutdown.

    Cancels all in-flight event processing tasks so pending LLM HTTP
    requests are aborted immediately instead of blocking shutdown.
    """
    global _consumer_task
    _shutdown_event.set()

    # Cancel in-flight processing tasks (aborts pending LLM calls)
    for task in list(_inflight_tasks):
        task.cancel()
    # Give cancelled tasks a moment to handle CancelledError and mark events
    if _inflight_tasks:
        await asyncio.gather(*list(_inflight_tasks), return_exceptions=True)
    _inflight_tasks.clear()

    if _consumer_task and not _consumer_task.done():
        try:
            await asyncio.wait_for(_consumer_task, timeout=5)
        except asyncio.TimeoutError:
            _consumer_task.cancel()
    _consumer_task = None
