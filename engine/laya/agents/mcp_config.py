"""Build the MCP config that is passed to spawned CLI agents.

Each spawned Claude Code session launches its own stdio MCP server subprocess
(`python -m laya.mcp`) so it can call back into Laya (search cards, fetch a
card by id, semantic search, etc.) while it works on the user's task.

See `engine/laya/mcp/server.py` for the server itself. We reuse the engine's
own Python interpreter (`sys.executable`) so the child shares the same venv
and has the `laya` package importable without extra PATH plumbing.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Laya MCP tools exposed to spawned agents. Read-only by design — writes,
# approvals, and egress are intentionally excluded for v1 so a runaway agent
# can inspect but not mutate the user's cards.
LAYA_MCP_READ_TOOLS: tuple[str, ...] = (
    "search_cards",
    "get_card",
    "get_card_stats",
    "get_cards_for_event",
    "search_events",
    "get_event",
    "search_entities",
    "get_entity",
    "get_recent_activity",
    "semantic_search",
)

LAYA_MCP_SERVER_NAME = "laya"


def build_laya_mcp_config(space_id: str | None) -> dict[str, Any]:
    """Return the JSON-shaped MCP config for `claude --mcp-config`.

    The spawned child inherits the engine's venv via ``sys.executable``, and
    ``LAYA_MCP_SKIP_MIGRATIONS=1`` tells the child MCP server not to re-run
    migrations (the engine already ran them; concurrent runs would race).
    """
    env: dict[str, str] = {"LAYA_MCP_SKIP_MIGRATIONS": "1"}
    if space_id:
        env["LAYA_SPACE_ID"] = space_id

    return {
        "mcpServers": {
            LAYA_MCP_SERVER_NAME: {
                "command": sys.executable,
                "args": ["-m", "laya.mcp"],
                "env": env,
            }
        }
    }


def build_laya_mcp_config_json(space_id: str | None) -> str:
    """JSON-serialized form suitable for passing inline to `--mcp-config`."""
    return json.dumps(build_laya_mcp_config(space_id))


def laya_allowed_tool_flags() -> list[str]:
    """Return `--allowedTools` argument pairs for the Laya MCP tools.

    Claude Code's allowlist syntax in non-interactive mode accepts either
    repeated `--allowedTools <name>` flags or a comma-separated list. We
    repeat the flag to match the existing pattern in claude_code.py.
    """
    flags: list[str] = []
    for tool in LAYA_MCP_READ_TOOLS:
        flags.extend(["--allowedTools", f"mcp__{LAYA_MCP_SERVER_NAME}__{tool}"])
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
