"""Connection health monitor — periodic validation and OAuth token refresh.

Runs as a background task to:
1. Check API-key connections are still valid
2. Refresh OAuth tokens before they expire
3. Update connection status in the database
4. Broadcast status changes via WebSocket
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog

from laya.db.sqlite import get_db
from laya.egress.connections import _get_from_keychain, _validate_credentials

log = structlog.get_logger()

# Check interval: 30 minutes
HEALTH_CHECK_INTERVAL = 1800

# OAuth tokens expire in ~1 hour; refresh 10 minutes before
OAUTH_REFRESH_THRESHOLD = 600

_health_task: asyncio.Task | None = None


async def start_health_monitor() -> None:
    """Start the background health check task."""
    global _health_task
    if _health_task is not None:
        return

    _health_task = asyncio.create_task(_health_loop())
    log.info("egress_health_monitor_started")


async def stop_health_monitor() -> None:
    """Stop the background health check task."""
    global _health_task
    if _health_task is not None:
        _health_task.cancel()
        try:
            await _health_task
        except asyncio.CancelledError:
            pass
        _health_task = None
        log.info("egress_health_monitor_stopped")


async def _health_loop() -> None:
    """Main health check loop."""
    while True:
        try:
            await _run_health_checks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("health_check_error", error=str(e))

        await asyncio.sleep(HEALTH_CHECK_INTERVAL)


async def _run_health_checks() -> None:
    """Run health checks on all connections."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT connection_id, platform, status, n8n_credential_id
           FROM egress_connections"""
    )

    if not rows:
        return

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    status_changes = []

    for row in rows:
        connection_id = row["connection_id"]
        platform = row["platform"]
        old_status = row["status"]

        # Check OAuth token expiry
        if platform in ("gmail", "calendar", "outlook", "outlook_calendar"):
            refreshed = await _check_oauth_token(connection_id, platform)
            new_status = "connected" if refreshed else old_status
        else:
            # API-key platforms: validate credentials
            credentials = _get_from_keychain(connection_id, platform)
            if credentials:
                valid, error = await _validate_credentials(platform, credentials)
                new_status = "connected" if valid else "error"
                error_msg = error if not valid else None
            else:
                new_status = "error"
                error_msg = "Credentials not found in keychain"

        # Update DB if status changed
        if new_status != old_status:
            await db.execute(
                """UPDATE egress_connections
                   SET status = ?, error_message = ?, last_validated_at = ?, updated_at = ?
                   WHERE connection_id = ?""",
                (
                    new_status,
                    error_msg if new_status == "error" else None,
                    now,
                    now,
                    connection_id,
                ),
            )
            status_changes.append({
                "connection_id": connection_id,
                "platform": platform,
                "old_status": old_status,
                "new_status": new_status,
            })
            log.info(
                "connection_status_changed",
                connection_id=connection_id,
                platform=platform,
                old_status=old_status,
                new_status=new_status,
            )
        else:
            # Just update last_validated_at
            await db.execute(
                "UPDATE egress_connections SET last_validated_at = ?, updated_at = ? WHERE connection_id = ?",
                (now, now, connection_id),
            )

    if status_changes:
        await db.commit()

        # Broadcast status changes
        try:
            from laya.api.websocket import manager

            for change in status_changes:
                await manager.broadcast({
                    "type": "connection_status",
                    "connection_id": change["connection_id"],
                    "platform": change["platform"],
                    "status": change["new_status"],
                })
        except Exception:
            pass  # WebSocket broadcast is best-effort


async def _check_oauth_token(connection_id: str, platform: str) -> bool:
    """Check if an OAuth token needs refreshing, and refresh if needed."""
    credentials = _get_from_keychain(connection_id, platform)
    if not credentials:
        return False

    obtained_at = credentials.get("obtained_at", 0)
    expires_in = credentials.get("expires_in", 3600)
    time_remaining = (obtained_at + expires_in) - time.time()

    if time_remaining > OAUTH_REFRESH_THRESHOLD:
        return True  # Token still valid, no refresh needed

    # Token expired or about to expire — refresh
    try:
        from laya.egress.oauth import refresh_access_token

        return await refresh_access_token(connection_id, platform)
    except Exception as e:
        log.error("oauth_refresh_failed", connection_id=connection_id, error=str(e))
        return False
