"""Briefing scheduler — asyncio loop that checks if it's briefing time."""

from __future__ import annotations

import asyncio
from datetime import datetime

import structlog

from laya.config import load_settings

log = structlog.get_logger()

_scheduler_task: asyncio.Task | None = None
_last_briefing_date: str | None = None


async def _scheduler_loop() -> None:
    """Main scheduler loop — runs every 60 seconds."""
    global _last_briefing_date

    while True:
        await asyncio.sleep(60)

        try:
            settings = load_settings()
            briefing_cfg = settings.get("briefing", {})

            if not briefing_cfg.get("enabled", False):
                continue

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
            today_str = now.strftime("%Y-%m-%d")

            if (
                now.hour == target_h
                and now.minute == target_m
                and _last_briefing_date != today_str
            ):
                _last_briefing_date = today_str
                log.info("scheduler_briefing_triggered", date=today_str)

                from laya.pipeline.briefing import generate_briefing

                try:
                    card_id = await generate_briefing()
                    log.info("scheduler_briefing_complete", card_id=card_id)
                except Exception as e:
                    log.error("scheduler_briefing_failed", error=str(e))
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
