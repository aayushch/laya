# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Build the MCP config that is passed to spawned CLI agents.

Spawned Claude Code sessions connect to the **same** HTTP/SSE MCP endpoint
that external clients (Claude Desktop, Cursor, VS Code) use. There is one MCP
transport in Laya — the FastAPI-mounted `/mcp/sse` route — and every caller
goes through it. This module just builds the per-spawn config dict.

The user's Settings → MCP toggles (read / write / egress) gate what tools the
server returns, and the per-spawn `--allowedTools` flags derived here narrow
that further to what Claude Code is allowed to call autonomously in
non-interactive `-p` mode.
"""

from __future__ import annotations

import json
from typing import Any

from laya.config import ENGINE_HOST, ENGINE_PORT, load_settings
from laya.mcp.scope import enabled_tool_names
from laya.security.keychain import get_mcp_token

LAYA_MCP_SERVER_NAME = "laya"


def _sse_url(space_id: str | None) -> str:
    base = f"http://{ENGINE_HOST}:{ENGINE_PORT}/mcp/sse"
    if space_id:
        return f"{base}?space_id={space_id}"
    return base


def _auth_headers() -> dict[str, str]:
    mcp_cfg = (load_settings().get("mcp", {}) or {})
    if mcp_cfg.get("auth_mode", "bearer") != "bearer":
        return {}
    token = get_mcp_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def build_laya_mcp_config(space_id: str | None) -> dict[str, Any]:
    """Return the JSON-shaped MCP config for `claude --mcp-config`.

    Uses the running engine's `/mcp/sse` endpoint with the user's current
    bearer token (when bearer auth is enabled). No subprocess, no env vars —
    the engine is already running.
    """
    return {
        "mcpServers": {
            LAYA_MCP_SERVER_NAME: {
                "type": "sse",
                "url": _sse_url(space_id),
                "headers": _auth_headers(),
            }
        }
    }


def build_laya_mcp_config_json(space_id: str | None) -> str:
    """JSON-serialized form suitable for passing inline to `--mcp-config`."""
    return json.dumps(build_laya_mcp_config(space_id))


def laya_allowed_tool_flags() -> list[str]:
    """Return `--allowedTools` flag pairs for the tools the user currently
    has enabled in Settings → MCP.

    Claude Code in non-interactive `-p` mode rejects any tool not explicitly
    allowlisted, so this list must match the user's enabled scopes — otherwise
    the agent will hang on a permission prompt that never gets answered.
    """
    scopes = (load_settings().get("mcp", {}) or {}).get("tool_scopes", {}) or {}
    flags: list[str] = []
    for tool_name in sorted(enabled_tool_names(scopes)):
        flags.extend(["--allowedTools", f"mcp__{LAYA_MCP_SERVER_NAME}__{tool_name}"])
    return flags


MCP_PROMPT_HINT = (
    "You have access to Laya's internal data via MCP tools (prefixed `mcp__laya__`). "
    "Use `mcp__laya__search_cards` or `mcp__laya__semantic_search` to find cards by "
    "keyword or natural-language phrase (e.g. \"cab payment related cards\"), and "
    "`mcp__laya__get_card` to fetch full details by card_id. Prefer these over guessing."
)


def augment_prompt_with_mcp_hint(prompt: str) -> str:
    """Prepend a short hint so the agent knows Laya's MCP tools exist."""
    return f"{MCP_PROMPT_HINT}\n\n---\n\n{prompt}"
