# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Background sweep for the agent-upload staging directory.

Files uploaded via /upload-agent-file land in ~/.laya/tmp/agent-staging/ until
the user clicks Run Agent. On submit they are moved into the card's
attachments folder. Files left behind (modal closed without submit, POST
succeeded but client dropped, etc.) are cleaned up here.

Runs once at startup and then every hour. A startup-only sweep is not enough
on macOS, where users commonly leave the app open for days or weeks.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

SWEEP_INTERVAL_SECONDS = 3600  # 1 hour
MAX_AGE_SECONDS = 24 * 3600  # 24 hours

_sweep_task: asyncio.Task | None = None


def sweep_agent_staging(max_age_seconds: int = MAX_AGE_SECONDS) -> int:
    """Delete files in ~/.laya/tmp/agent-staging/ older than max_age_seconds.

    Returns the number of files deleted. Safe to call at any time; missing
    directory is a no-op.
    """
    from laya.config import LAYA_HOME

    staging_dir = LAYA_HOME / "tmp" / "agent-staging"
    if not staging_dir.exists():
        return 0

    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in staging_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError as e:
            log.debug("agent_staging_unlink_failed", path=str(entry), error=str(e))
    if removed:
        log.info("agent_staging_swept", removed=removed)
    return removed


async def _sweep_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sweep_agent_staging)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("agent_staging_sweep_error", error=str(e))
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start_staging_sweeper() -> None:
    """Start the periodic staging sweep task."""
    global _sweep_task
    if _sweep_task is not None:
        return
    _sweep_task = asyncio.create_task(_sweep_loop(), name="agent_staging_sweeper")
    log.info("agent_staging_sweeper_started")


async def stop_staging_sweeper() -> None:
    global _sweep_task
    if _sweep_task is None:
        return
    _sweep_task.cancel()
    try:
        await _sweep_task
    except asyncio.CancelledError:
        pass
    _sweep_task = None
    log.info("agent_staging_sweeper_stopped")
