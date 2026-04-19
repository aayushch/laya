"""Laya MCP server — exposes Laya's internal tools over stdio transport.

Usage:
    python -m laya.mcp.server

This starts a stdio-based MCP server that any MCP-compatible client
(Claude Desktop, VS Code, etc.) can connect to for querying Laya's
cards, events, entities, and performing actions.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import structlog

log = structlog.get_logger()


def _build_mcp_server():
    """Build and configure the MCP server with all Laya tools."""
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    from laya.llm.tools.definitions import get_all_tool_definitions
    from laya.llm.tools.executor import execute_tool

    server = Server("laya")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return all available Laya tools in MCP format."""
        defs = get_all_tool_definitions()
        tools = []
        for d in defs:
            fn = d["function"]
            tools.append(
                Tool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    inputSchema=fn.get("parameters", {"type": "object", "properties": {}}),
                )
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
        """Execute a Laya tool and return the result."""
        # When spawned for a specific agent session, LAYA_SPACE_ID scopes
        # tool calls to that space so the agent only sees the right cards.
        space_id = os.environ.get("LAYA_SPACE_ID") or None
        result_str = await execute_tool(name, arguments or {}, space_id=space_id)
        return [TextContent(type="text", text=result_str)]

    return server


async def _run_server() -> None:
    """Initialize Laya services and run the MCP server on stdio."""
    from mcp.server.stdio import stdio_server

    from laya.config import ensure_directories
    from laya.db.chromadb_store import connect_chromadb, disconnect_chromadb
    from laya.db.migrate import run_migrations
    from laya.db.sqlite import connect, disconnect
    from laya.logging_setup import setup_logging
    from laya.security.keychain import load_all_keys_to_env

    # Initialize services
    setup_logging()
    ensure_directories()
    db = await connect()

    # When spawned as a child of a running engine (agent MCP integration), the
    # parent has already applied migrations. Re-running them concurrently
    # across several agent sessions races on the SQLite schema lock.
    if os.environ.get("LAYA_MCP_SKIP_MIGRATIONS") != "1":
        await run_migrations(db)

    load_all_keys_to_env()
    connect_chromadb()

    log.info(
        "mcp_server_starting",
        space_id=os.environ.get("LAYA_SPACE_ID"),
        skip_migrations=os.environ.get("LAYA_MCP_SKIP_MIGRATIONS") == "1",
    )

    server = _build_mcp_server()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        disconnect_chromadb()
        await disconnect()
        log.info("mcp_server_stopped")


def main() -> None:
    """Entry point for `python -m laya.mcp.server`."""
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
