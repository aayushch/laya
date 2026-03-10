"""Space resolution — map incoming events to spaces via their source workflow ID."""

from __future__ import annotations

import uuid

import structlog

from laya.db.sqlite import get_db

log = structlog.get_logger()

# Legacy hardcoded connection_id values from pre-Spaces workflows.
# Used as fallback when workflow hasn't been updated to use $workflow.id yet.
_LEGACY_PLATFORM_MAP = {
    "gmail_main": "gmail",
    "jira_main": "jira",
    "slack_main": "slack",
    "calendar_main": "calendar",
    "bb_main": "bitbucket",
}


async def resolve_space(event_id: str, connection_id: str | None, platform: str) -> str | None:
    """Resolve the space_id for an incoming event.

    Lookup chain:
    1. Match connection_id against sources.workflow_id
    2. If not found, check if it's a legacy hardcoded value and match by platform
    3. If still not found, auto-create a source in the Default space
    4. If connection_id is None, return None (unresolvable)

    Returns the space_id, or None if connection_id is missing.
    """
    if not connection_id:
        return None

    db = await get_db()

    # 1. Direct lookup by workflow_id
    rows = await db.execute_fetchall(
        "SELECT space_id FROM sources WHERE workflow_id = ?",
        (connection_id,),
    )
    if rows:
        space_id = rows[0]["space_id"]
        await _update_event_space(db, event_id, space_id)
        return space_id

    # 2. Legacy fallback — check if this is a known hardcoded value
    if connection_id in _LEGACY_PLATFORM_MAP:
        legacy_platform = _LEGACY_PLATFORM_MAP[connection_id]
        # Auto-register as a source in the default space
        source_id = f"src_{uuid.uuid4().hex[:12]}"
        source_name = f"{legacy_platform.title()} (default)"
        try:
            await db.execute(
                """INSERT OR IGNORE INTO sources (source_id, name, platform, workflow_id, space_id)
                   VALUES (?, ?, ?, ?, 'default')""",
                (source_id, source_name, legacy_platform, connection_id),
            )
            await db.commit()
            log.info("legacy_source_auto_registered",
                     workflow_id=connection_id, platform=legacy_platform)
        except Exception as e:
            log.warning("legacy_source_register_failed", error=str(e))

        await _update_event_space(db, event_id, "default")
        return "default"

    # 3. Auto-discover — unknown workflow_id, register as new source in Default space
    source_id = f"src_{uuid.uuid4().hex[:12]}"
    source_name = f"{platform.title()} ({connection_id[:8]})"
    try:
        await db.execute(
            """INSERT OR IGNORE INTO sources (source_id, name, platform, workflow_id, space_id)
               VALUES (?, ?, ?, ?, 'default')""",
            (source_id, source_name, platform, connection_id),
        )
        await db.commit()
        log.info("source_auto_discovered",
                 workflow_id=connection_id, platform=platform, source_id=source_id)
    except Exception as e:
        log.warning("source_auto_discover_failed", error=str(e))

    await _update_event_space(db, event_id, "default")
    return "default"


async def _update_event_space(db, event_id: str, space_id: str) -> None:
    """Set the space_id on the events table row."""
    await db.execute(
        "UPDATE events SET space_id = ? WHERE event_id = ?",
        (space_id, event_id),
    )
    await db.commit()
