"""Briefing scheduler and housekeeping — asyncio loop that runs every 60 seconds."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from laya.config import load_settings

log = structlog.get_logger()

_scheduler_task: asyncio.Task | None = None
_last_briefing_date: str | None = None
_last_housekeeping_date: str | None = None

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


async def _scheduler_loop() -> None:
    """Main scheduler loop — runs every 60 seconds."""
    global _last_briefing_date, _last_housekeeping_date

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
                    try:
                        card_id = await generate_briefing()
                        log.info("scheduler_briefing_complete", card_id=card_id)
                    except Exception as e:
                        log.error("scheduler_briefing_failed", error=str(e))

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
