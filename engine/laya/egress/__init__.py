"""Laya Egress — outbound action system.

This module owns ALL outbound communication with external platforms.
The Engine is a consumer — it calls these functions, never builds
n8n payloads or knows about webhook URLs directly.

Public API:
    execute()          — Execute an outbound action
    preview()          — Preview what an action would do (without executing)
    get_capabilities() — What actions can a platform perform?
    list_connections()  — List configured platform connections
    connect()          — Set up a new platform connection
    disconnect()       — Remove a platform connection
"""

from __future__ import annotations

from laya.egress.models import (
    Connection,
    ConnectionResult,
    EgressCapability,
    EgressPreview,
    EgressRequest,
    EgressResult,
)
from laya.egress.registry import get_capabilities as _get_caps
from laya.egress.router import build_preview, route_and_execute


async def execute(request: EgressRequest) -> EgressResult:
    """Execute an outbound action.

    This is the primary entry point. The Engine calls this for card action
    execution, chat tool execution, and UI-initiated actions.

    The egress module determines HOW to execute (n8n, SMTP, MCP, etc.)
    based on the platform and available backends.
    """
    return await route_and_execute(request)


async def preview(request: EgressRequest) -> EgressPreview:
    """Return a preview of what an action would do without executing.

    Used by the chat confirmation flow and the UI action preview modal.
    """
    return await build_preview(request)


async def get_capabilities(platform: str) -> list[EgressCapability]:
    """What actions can this platform perform?

    Used by the stager (to generate suggested_actions), the UI (to show
    available quick-action buttons), and the chat LLM (to know what's possible).
    """
    return _get_caps(platform)


async def list_connections() -> list[Connection]:
    """List all configured platform connections and their health status.

    Reads from the egress_connections table in SQLite.
    """
    # Phase 1: delegate to connection broker (implemented in connections.py)
    from laya.egress.connections import list_all_connections

    return await list_all_connections()


async def connect(platform: str, credentials: dict, name: str | None = None, space_id: str | None = None) -> ConnectionResult:
    """Set up a new platform connection.

    Validates credentials, stores in keychain, and provisions to n8n.
    Single entry point — user never needs to touch n8n.
    """
    from laya.egress.connections import create_connection

    return await create_connection(platform, credentials, name=name, space_id=space_id)


async def disconnect(connection_id: str) -> None:
    """Remove a platform connection.

    Cleans up from keychain and n8n.
    """
    from laya.egress.connections import remove_connection

    await remove_connection(connection_id)


__all__ = [
    "execute",
    "preview",
    "get_capabilities",
    "list_connections",
    "connect",
    "disconnect",
    "EgressRequest",
    "EgressResult",
    "EgressPreview",
    "EgressCapability",
    "Connection",
    "ConnectionResult",
]
