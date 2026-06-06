# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""HTTP/SSE MCP server.

Builds the MCP `Server` instance whose tools are filtered by the user's
Settings → MCP scope toggles and whose per-call space scoping comes from the
`space_id` query parameter passed on the SSE handshake (legacy) or the
Streamable HTTP session (current).

This module owns:
- A module-level shared `SseServerTransport` (legacy, path-routed via FastAPI
  in `laya.api.mcp_api`).
- A `StreamableHTTPSessionManager` for the current Streamable HTTP transport.
- A `Server` builder that re-reads settings on every list_tools / call_tool so
  toggle changes take effect with no engine restart.
- A `contextvar` for the current request's space_id, set by the transport
  handler before `server.run()` is awaited; inherited by all child tasks the
  SDK spawns to process incoming JSON-RPC requests.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid

import structlog
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND, ErrorData, TextContent, Tool

from laya.config import load_settings
from laya.llm.tools.definitions import get_all_tool_definitions
from laya.llm.tools.executor import execute_tool
from laya.mcp.scope import enabled_tool_names

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Legacy SSE transport
# ---------------------------------------------------------------------------

# Mounted at /mcp/messages/ in laya.api.mcp_api; SseServerTransport requires a
# RELATIVE path (it rejects absolute URLs).
SSE_MESSAGES_PATH = "/mcp/messages/"

sse_transport = SseServerTransport(SSE_MESSAGES_PATH)

# Per-request space scope. Set by the SSE route handler; child tasks the MCP
# SDK spawns inside `server.run()` inherit the value via contextvar copy.
current_space_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "laya_mcp_current_space_id", default=None
)

# ---------------------------------------------------------------------------
# Streamable HTTP session manager
# ---------------------------------------------------------------------------

# Loopback server — disable DNS-rebinding protection so Claude Desktop
# (which connects from a local Electron process) isn't blocked by Origin/Host
# header checks.
_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


class _Session:
    """One Streamable HTTP session: a transport + a background server.run() task."""

    __slots__ = ("transport", "task", "space_id")

    def __init__(
        self,
        transport: StreamableHTTPServerTransport,
        task: asyncio.Task,  # type: ignore[type-arg]
        space_id: str | None,
    ) -> None:
        self.transport = transport
        self.task = task
        self.space_id = space_id


class StreamableSessionManager:
    """Manages Streamable HTTP sessions (transport + server pairs).

    Claude Desktop (and other MCP clients) send an ``mcp-session-id`` header
    on every request after the initial ``initialize``.  The manager creates a
    new session for ``initialize`` POSTs and routes subsequent requests to the
    matching session.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    async def create_session(self, space_id: str | None = None) -> _Session:
        session_id = uuid.uuid4().hex
        transport = StreamableHTTPServerTransport(
            mcp_session_id=session_id,
            security_settings=_SECURITY,
        )
        server = build_mcp_server()

        ready = asyncio.Event()

        async def _run() -> None:
            token = current_space_id.set(space_id)
            try:
                async with transport.connect() as (read_stream, write_stream):
                    ready.set()
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
            finally:
                current_space_id.reset(token)
                self._sessions.pop(session_id, None)

        task = asyncio.create_task(_run())
        await ready.wait()

        session = _Session(transport=transport, task=task, space_id=space_id)
        self._sessions[session_id] = session
        log.info("mcp_streamable_session_created", session_id=session_id, space_id=space_id)
        return session

    def get(self, session_id: str) -> _Session | None:
        return self._sessions.get(session_id)

    async def shutdown(self) -> None:
        """Terminate all sessions (engine shutdown)."""
        for sid, session in list(self._sessions.items()):
            session.transport.terminate()
            session.task.cancel()
        self._sessions.clear()


streamable_sessions = StreamableSessionManager()


def _current_scopes() -> dict[str, bool]:
    return load_settings().get("mcp", {}).get("tool_scopes", {}) or {}


def build_mcp_server() -> Server:
    """Construct the MCP server with scope-aware handlers.

    Settings are re-read inside each handler so toggling scope or auth in the
    UI takes effect on the next call without restarting the engine.
    """
    server: Server = Server("laya")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        allowed = enabled_tool_names(_current_scopes())
        return [
            Tool(
                name=fn["function"]["name"],
                description=fn["function"].get("description", ""),
                inputSchema=fn["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            )
            for fn in get_all_tool_definitions()
            if fn["function"]["name"] in allowed
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None = None) -> list[TextContent]:
        allowed = enabled_tool_names(_current_scopes())
        if name not in allowed:
            # The user's Settings → MCP toggles don't enable the scope this
            # tool belongs to. Surface a clear JSON-RPC error to the client.
            raise McpError(
                ErrorData(
                    code=METHOD_NOT_FOUND,
                    message=(
                        f"Tool '{name}' is not enabled in the current MCP scope. "
                        "Toggle the corresponding scope (read / write / egress) "
                        "in Settings → MCP."
                    ),
                )
            )
        space_id = current_space_id.get()
        result = await execute_tool(name, arguments or {}, space_id=space_id)
        return [TextContent(type="text", text=result)]

    return server
