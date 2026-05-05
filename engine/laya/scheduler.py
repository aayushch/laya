"""Briefing scheduler and housekeeping — asyncio loop that runs every 60 seconds."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from laya.config import load_settings

log = structlog.get_logger()

_scheduler_task: asyncio.Task | None = None
_last_briefing_date: str | None = None
_last_omni_date: str | None = None
_last_omni_rolling: datetime | None = None
_last_housekeeping_date: str | None = None
_last_budget_month: str | None = None
_last_learn_check: datetime | None = None
_last_context_learn_check: datetime | None = None
_threshold_cooldowns: dict[str, datetime] = {}
_THRESHOLD_COOLDOWN_MINUTES = 10

# Statuses that are safe to auto-delete (never auto-delete active/in-progress cards)
_HOUSEKEEPING_STATUSES = ("archived", "dismissed", "done", "failed")


async def _run_housekeeping(retention_days: int) -> None:
    """Delete cards in terminal states that are older than `retention_days` days."""
    from laya.db.sqlite import get_db
    from laya.api.cards_api import _delete_card_cascade

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()
    placeholders = ",".join("?" * len(_HOUSEKEEPING_STATUSES))
    rows = await db.execute_fetchall(
        f"SELECT card_id, event_id FROM action_cards "
        f"WHERE status IN ({placeholders}) AND created_at < ?",
        (*_HOUSEKEEPING_STATUSES, cutoff),
    )

    if not rows:
        log.info("housekeeping_nothing_to_delete", retention_days=retention_days)
        return

    deleted = 0
    for row in rows:
        try:
            await _delete_card_cascade(db, row["card_id"], row["event_id"])
            deleted += 1
        except Exception as e:
            log.warning("housekeeping_card_delete_failed", card_id=row["card_id"], error=str(e))

    await db.commit()
    log.info("housekeeping_complete", deleted=deleted, retention_days=retention_days)


async def _run_chat_housekeeping(retention_days: int) -> None:
    """Delete chat conversations that have been idle longer than `retention_days` days."""
    from laya.db.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT conversation_id FROM chat_conversations WHERE updated_at < ?",
        (cutoff,),
    )

    if not rows:
        log.info("chat_housekeeping_nothing_to_delete", retention_days=retention_days)
        return

    deleted = 0
    for row in rows:
        try:
            conv_id = row["conversation_id"]
            await db.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
            await db.execute("DELETE FROM chat_conversations WHERE conversation_id = ?", (conv_id,))
            deleted += 1
        except Exception as e:
            log.warning("chat_housekeeping_delete_failed", conversation_id=row["conversation_id"], error=str(e))

    await db.commit()
    log.info("chat_housekeeping_complete", deleted=deleted, retention_days=retention_days)


async def _run_trace_housekeeping(retention_days: int) -> None:
    """Delete traces older than `retention_days` days."""
    from laya.db.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT trace_id FROM traces WHERE created_at < ?",
        (cutoff,),
    )

    if not rows:
        log.info("trace_housekeeping_nothing_to_delete", retention_days=retention_days)
        return

    deleted = 0
    for row in rows:
        try:
            await db.execute("DELETE FROM traces WHERE trace_id = ?", (row["trace_id"],))
            deleted += 1
        except Exception as e:
            log.warning("trace_housekeeping_delete_failed", trace_id=row["trace_id"], error=str(e))

    await db.commit()
    log.info("trace_housekeeping_complete", deleted=deleted, retention_days=retention_days)


async def _run_corrections_housekeeping(retention_days: int) -> None:
    """Delete processed corrections older than `retention_days` days.

    Applies to both classification_corrections and context_corrections.
    Only deletes rows that have already been processed by the learner
    (processed=1), keeping unprocessed corrections for future learning.
    """
    from laya.db.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    db = await get_db()
    total_deleted = 0

    for table in ("classification_corrections", "context_corrections"):
        try:
            cursor = await db.execute(
                f"DELETE FROM {table} WHERE processed = 1 AND created_at < ?",
                (cutoff,),
            )
            if cursor.rowcount:
                total_deleted += cursor.rowcount
        except Exception as e:
            log.debug("corrections_housekeeping_table_skip", table=table, error=str(e))

    await db.commit()
    if total_deleted:
        log.info("corrections_housekeeping_complete", deleted=total_deleted, retention_days=retention_days)


async def _run_audit_housekeeping(retention_days: int) -> None:
    """Delete audit_log entries older than `retention_days` days.

    This catches orphaned entries (no card_id, or card already deleted)
    and keeps the table bounded regardless of card lifecycle.
    """
    from laya.db.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
    )
    await db.commit()
    deleted = cursor.rowcount
    if deleted:
        log.info("audit_housekeeping_complete", deleted=deleted, retention_days=retention_days)
    else:
        log.info("audit_housekeeping_nothing_to_delete", retention_days=retention_days)


async def _run_ingestion_errors_housekeeping(retention_days: int) -> None:
    """Delete ingestion error rows older than `retention_days` days."""
    from laya.db.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM ingestion_errors WHERE last_occurred_at < ?", (cutoff,)
    )
    await db.commit()
    deleted = cursor.rowcount
    if deleted:
        log.info("ingestion_errors_housekeeping_complete", deleted=deleted, retention_days=retention_days)
    else:
        log.info("ingestion_errors_housekeeping_nothing_to_delete", retention_days=retention_days)


async def _run_omni_housekeeping(retention_days: int) -> None:
    """Delete omni snapshots older than `retention_days` days.

    Always preserves the latest snapshot per space regardless of age
    so the user never sees an empty Omni page. Also protects base
    snapshots that have active delta dependents within the retention window.
    """
    from laya.db.sqlite import get_db
    from laya.pipeline.omni import _latest_cache

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    db = await get_db()

    # Find the latest snapshot_id per space (these are preserved even if old)
    latest_rows = await db.execute_fetchall(
        """SELECT snapshot_id FROM omni_snapshots
           WHERE (space_id, version) IN (
               SELECT space_id, MAX(version) FROM omni_snapshots GROUP BY space_id
           )"""
    )
    protected_ids = {row["snapshot_id"] for row in latest_rows}

    # Protect base snapshots that have delta dependents within the retention window.
    # Without their base, those deltas can't be reconstructed.
    base_rows = await db.execute_fetchall(
        """SELECT DISTINCT s2.snapshot_id
           FROM omni_snapshots s1
           JOIN omni_snapshots s2 ON s1.space_id = s2.space_id AND s1.base_version = s2.version
           WHERE s1.is_delta = 1 AND s1.created_at >= ? AND s2.is_delta = 0""",
        (cutoff,),
    )
    for row in base_rows:
        protected_ids.add(row["snapshot_id"])

    # Find candidates for deletion
    rows = await db.execute_fetchall(
        "SELECT snapshot_id, space_id FROM omni_snapshots WHERE created_at < ?",
        (cutoff,),
    )

    to_delete = [row["snapshot_id"] for row in rows if row["snapshot_id"] not in protected_ids]
    affected_spaces = {row["space_id"] for row in rows if row["snapshot_id"] not in protected_ids}

    if not to_delete:
        log.info("omni_housekeeping_nothing_to_delete", retention_days=retention_days)
        return

    placeholders = ",".join("?" for _ in to_delete)
    await db.execute(
        f"DELETE FROM omni_snapshots WHERE snapshot_id IN ({placeholders})",
        to_delete,
    )
    await db.commit()

    # Invalidate cache for affected spaces
    for sid in affected_spaces:
        _latest_cache.pop(sid, None)

    log.info("omni_housekeeping_complete", deleted=len(to_delete), retention_days=retention_days)


async def _scheduler_loop() -> None:
    """Main scheduler loop — runs every 60 seconds."""
    global _last_briefing_date, _last_housekeeping_date, _last_budget_month, _last_learn_check, _last_omni_rolling, _last_context_learn_check

    while True:
        await asyncio.sleep(60)

        try:
            settings = load_settings()
            now_utc = datetime.now(timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")

            # --- Briefing ---
            briefing_cfg = settings.get("briefing", {})
            if briefing_cfg.get("enabled", False):
                tz_name = briefing_cfg.get("timezone", "America/New_York")
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(tz_name)
                except Exception:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo("UTC")

                now = datetime.now(tz)
                target_time = briefing_cfg.get("time", "07:00")
                target_h, target_m = map(int, target_time.split(":"))
                today_local = now.strftime("%Y-%m-%d")

                if (
                    now.hour == target_h
                    and now.minute == target_m
                    and _last_briefing_date != today_local
                ):
                    _last_briefing_date = today_local
                    log.info("scheduler_briefing_triggered", date=today_local)
                    from laya.pipeline.briefing import generate_briefing

                    if briefing_cfg.get("per_space", False):
                        # Per-space mode: one briefing per non-default space
                        from laya.db.sqlite import get_db as _get_briefing_db
                        _bdb = await _get_briefing_db()
                        _space_rows = await _bdb.execute_fetchall(
                            "SELECT space_id FROM spaces WHERE is_default = 0"
                        )
                        for _row in _space_rows:
                            _sid = _row["space_id"]
                            try:
                                card_id = await generate_briefing(space_id=_sid)
                                log.info("scheduler_briefing_complete", card_id=card_id, space_id=_sid)
                            except Exception as e:
                                log.error("scheduler_briefing_failed", error=str(e), space_id=_sid)
                    else:
                        # Global mode: one briefing across all spaces
                        try:
                            card_id = await generate_briefing()
                            log.info("scheduler_briefing_complete", card_id=card_id)
                        except Exception as e:
                            log.error("scheduler_briefing_failed", error=str(e))

            # --- Omni resynthesis ---
            omni_cfg = settings.get("omni", {})
            if omni_cfg.get("enabled", False):
                omni_tz_name = omni_cfg.get("timezone", "America/New_York")
                try:
                    from zoneinfo import ZoneInfo
                    omni_tz = ZoneInfo(omni_tz_name)
                except Exception:
                    from zoneinfo import ZoneInfo
                    omni_tz = ZoneInfo("UTC")

                omni_now = datetime.now(omni_tz)
                omni_target = omni_cfg.get("resynthesis_time", "17:00")
                omni_h, omni_m = map(int, omni_target.split(":"))
                omni_today = omni_now.strftime("%Y-%m-%d")

                # (1) EOD resynthesis — fixed time of day
                if (
                    omni_now.hour == omni_h
                    and omni_now.minute == omni_m
                    and _last_omni_date != omni_today
                ):
                    _last_omni_date = omni_today
                    _last_omni_rolling = now_utc
                    log.info("scheduler_omni_resynthesis_triggered", trigger="eod", date=omni_today)
                    from laya.pipeline.omni import run_omni_resynthesis
                    try:
                        snapshot_ids = await run_omni_resynthesis()
                        log.info("scheduler_omni_resynthesis_complete", trigger="eod", snapshots=len(snapshot_ids))
                    except Exception as e:
                        log.error("scheduler_omni_resynthesis_failed", trigger="eod", error=str(e))
                else:
                    # (2) Rolling interval — every N hours
                    # (3) Event threshold — after N new events since last resynthesis
                    rolling_hours = int(omni_cfg.get("rolling_interval_hours", 4))
                    event_threshold = int(omni_cfg.get("event_threshold", 50))
                    should_roll = False
                    trigger_reason = ""
                    _threshold_spaces: list[str] = []

                    if rolling_hours > 0:
                        if _last_omni_rolling is None:
                            _last_omni_rolling = now_utc  # First tick — record, don't trigger
                        elif (now_utc - _last_omni_rolling) >= timedelta(hours=rolling_hours):
                            should_roll = True
                            trigger_reason = f"interval_{rolling_hours}h"

                    if not should_roll and event_threshold > 0:
                        # Check event threshold per-space so each space triggers
                        # resynthesis independently based on its own activity.
                        try:
                            from laya.db.sqlite import get_db
                            _db = await get_db()
                            _space_rows = await _db.execute_fetchall("SELECT space_id FROM spaces")
                            _space_ids = [r["space_id"] for r in _space_rows] if _space_rows else ["default"]
                            if "default" not in _space_ids:
                                _space_ids.append("default")

                            _cooldown_delta = timedelta(minutes=_THRESHOLD_COOLDOWN_MINUTES)
                            for _sid in _space_ids:
                                _cd = _threshold_cooldowns.get(_sid)
                                if _cd and (now_utc - _cd) < _cooldown_delta:
                                    continue

                                _last_synth = await _db.execute_fetchall(
                                    """SELECT generated_at FROM omni_snapshots
                                       WHERE snapshot_type IN ('scheduled', 'rolling', 'manual')
                                         AND space_id = ?
                                       ORDER BY version DESC LIMIT 1""",
                                    (_sid,),
                                )
                                # Normalize ISO 'T' separator to space to match SQLite CURRENT_TIMESTAMP format,
                                # otherwise string comparison fails (space 0x20 < 'T' 0x54).
                                _raw = _last_synth[0]["generated_at"] if _last_synth else None
                                _since = _raw.replace("T", " ").split("+")[0] if _raw else "2000-01-01 00:00:00"
                                _count_rows = await _db.execute_fetchall(
                                    "SELECT COUNT(*) AS cnt FROM action_cards WHERE space_id = ? AND created_at > ?",
                                    (_sid, _since),
                                )
                                _new_count = _count_rows[0]["cnt"] if _count_rows else 0
                                if _new_count >= event_threshold:
                                    _threshold_spaces.append(_sid)

                            if _threshold_spaces:
                                trigger_reason = f"threshold_{len(_threshold_spaces)}_spaces"
                        except Exception as e:
                            _threshold_spaces = []
                            log.warning("omni_event_threshold_check_failed", error=str(e))

                    if should_roll:
                        _last_omni_rolling = now_utc
                        log.info("scheduler_omni_resynthesis_triggered", trigger=trigger_reason)
                        from laya.pipeline.omni import run_omni_resynthesis
                        try:
                            snapshot_ids = await run_omni_resynthesis(snapshot_type="rolling")
                            log.info("scheduler_omni_resynthesis_complete", trigger=trigger_reason, snapshots=len(snapshot_ids))
                        except Exception as e:
                            log.error("scheduler_omni_resynthesis_failed", trigger=trigger_reason, error=str(e))
                    elif _threshold_spaces:
                        # Per-space threshold-triggered resynthesis
                        _last_omni_rolling = now_utc
                        from laya.pipeline.omni import run_omni_resynthesis
                        for _sid in _threshold_spaces:
                            log.info("scheduler_omni_resynthesis_triggered", trigger=f"threshold_space_{_sid}")
                            try:
                                snapshot_ids = await run_omni_resynthesis(space_id=_sid, snapshot_type="rolling")
                                log.info("scheduler_omni_resynthesis_complete", trigger=f"threshold_space_{_sid}", snapshots=len(snapshot_ids))
                                _threshold_cooldowns.pop(_sid, None)
                            except Exception as e:
                                _threshold_cooldowns[_sid] = now_utc
                                log.error("scheduler_omni_resynthesis_failed", trigger=f"threshold_space_{_sid}", error=str(e))

            # --- Housekeeping (once per day at ~00:00 UTC) ---
            if _last_housekeeping_date != today_str:
                _last_housekeeping_date = today_str
                retention_cfg = settings.get("retention", {})
                retention_days = int(retention_cfg.get("card_retention_days", 90))
                log.info("housekeeping_triggered", retention_days=retention_days)
                try:
                    await _run_housekeeping(retention_days)
                except Exception as e:
                    log.error("housekeeping_failed", error=str(e))

                # Chat conversation housekeeping
                chat_retention_days = int(retention_cfg.get("chat_retention_days", 90))
                try:
                    await _run_chat_housekeeping(chat_retention_days)
                except Exception as e:
                    log.error("chat_housekeeping_failed", error=str(e))

                # Trace housekeeping
                trace_retention_days = int(retention_cfg.get("trace_retention_days", 90))
                try:
                    await _run_trace_housekeeping(trace_retention_days)
                except Exception as e:
                    log.error("trace_housekeeping_failed", error=str(e))

                # Audit log housekeeping
                audit_retention_days = int(retention_cfg.get("audit_retention_days", 90))
                try:
                    await _run_audit_housekeeping(audit_retention_days)
                except Exception as e:
                    log.error("audit_housekeeping_failed", error=str(e))

                # Omni snapshot housekeeping
                omni_retention_days = int(retention_cfg.get("omni_retention_days", 30))
                try:
                    await _run_omni_housekeeping(omni_retention_days)
                except Exception as e:
                    log.error("omni_housekeeping_failed", error=str(e))

                # Processed corrections cleanup
                corrections_retention = int(settings.get("tuning", {}).get("corrections_retention_days", 30))
                try:
                    await _run_corrections_housekeeping(corrections_retention)
                except Exception as e:
                    log.error("corrections_housekeeping_failed", error=str(e))

                # Ingestion errors cleanup
                ingestion_errors_retention = int(retention_cfg.get("ingestion_errors_retention_days", 30))
                try:
                    await _run_ingestion_errors_housekeeping(ingestion_errors_retention)
                except Exception as e:
                    log.error("ingestion_errors_housekeeping_failed", error=str(e))

            # --- Budget month rollover (local timezone) ---
            try:
                tz_name = settings.get("briefing", {}).get("timezone", "America/New_York")
                from zoneinfo import ZoneInfo
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = ZoneInfo("UTC")
                current_month = datetime.now(tz).strftime("%Y-%m")

                if _last_budget_month is None:
                    # First tick — just record, don't trigger rollover
                    _last_budget_month = current_month
                elif _last_budget_month != current_month:
                    previous_month = _last_budget_month
                    _last_budget_month = current_month
                    log.info("scheduler_month_rollover", previous=previous_month, current=current_month)
                    from laya.pipeline.budget import on_month_rollover
                    await on_month_rollover(previous_month)
            except Exception as e:
                log.error("budget_month_rollover_failed", error=str(e))

            # --- Classification learning (every 6 hours) ---
            tuning = settings.get("tuning", {})
            cls_learn_hours = int(tuning.get("classification_learn_interval_hours", 6))
            if _last_learn_check is None or (now_utc - _last_learn_check) >= timedelta(hours=cls_learn_hours):
                _last_learn_check = now_utc
                try:
                    from laya.pipeline.learn import run_learn_all
                    await run_learn_all()
                except Exception as e:
                    log.error("learn_extraction_failed", error=str(e))

            # --- Context association learning ---
            ctx_learn_hours = int(tuning.get("context_learn_interval_hours", 6))
            if _last_context_learn_check is None or (now_utc - _last_context_learn_check) >= timedelta(hours=ctx_learn_hours):
                _last_context_learn_check = now_utc
                try:
                    from laya.pipeline.context_learn import run_context_learn_all
                    await run_context_learn_all()
                except Exception as e:
                    log.error("context_learn_extraction_failed", error=str(e))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("scheduler_loop_error", error=str(e))


def start_scheduler() -> None:
    """Start the briefing scheduler as a background task."""
    global _scheduler_task
    if _scheduler_task is not None:
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="briefing_scheduler")
    log.info("scheduler_started")


def stop_scheduler() -> None:
    """Stop the briefing scheduler."""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None
        log.info("scheduler_stopped")
