"""Tool execution dispatcher — routes tool calls to implementations."""

from __future__ import annotations

import json
from typing import Any

import structlog

from laya.egress.tool_handlers import (
    EGRESS_TOOL_NAMES,
    handle_confirm_egress,
    handle_egress_tool,
    handle_open_compose,
)
from laya.egress.tools import EGRESS_TOOL_NAMES as _EGRESS_NAMES
from laya.llm.tools import card_tools, entity_tools, event_tools, search_tools, settings_tools

log = structlog.get_logger()

# Registry: tool_name -> async handler function
_TOOL_HANDLERS: dict[str, Any] = {}


def _register_tools() -> None:
    """Build the handler registry from all tool modules."""
    global _TOOL_HANDLERS
    if _TOOL_HANDLERS:
        return

    _TOOL_HANDLERS = {
        # Read tools
        "search_cards": card_tools.search_cards,
        "get_card": card_tools.get_card,
        "get_card_stats": card_tools.get_card_stats,
        "get_cards_for_event": card_tools.get_cards_for_event,
        "search_events": event_tools.search_events,
        "get_event": event_tools.get_event,
        "get_recent_activity": event_tools.get_recent_activity,
        "search_entities": entity_tools.search_entities,
        "get_entity": entity_tools.get_entity,
        "semantic_search": search_tools.semantic_search,
        # Write tools
        "dismiss_card": card_tools.dismiss_card,
        "approve_card": card_tools.approve_card,
        "mark_card_done": card_tools.mark_card_done,
        "archive_card": card_tools.archive_card,
        "reopen_card": card_tools.reopen_card,
        # Settings tools
        "get_settings": settings_tools.get_settings,
        "update_theme": settings_tools.update_theme,
        "update_retention": settings_tools.update_retention,
        "update_briefing": settings_tools.update_briefing,
        "update_notifications": settings_tools.update_notifications,
        "update_feed_preferences": settings_tools.update_feed_preferences,
        "update_agent_execution_mode": settings_tools.update_agent_execution_mode,
    }


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    space_id: str | None = None,
) -> str:
    """Execute a tool by name and return the result as a JSON string.

    Args:
        name: The tool function name.
        arguments: The tool arguments from the LLM.
        space_id: Optional space context for filtering.

    Returns:
        JSON string result to feed back to the LLM.
    """
    _register_tools()

    # Egress tools — handled by the egress module
    if name == "open_compose":
        return await handle_open_compose(arguments, space_id)
    if name == "confirm_egress":
        return await handle_confirm_egress(arguments, space_id)
    if name in _EGRESS_NAMES:
        return await handle_egress_tool(name, arguments, space_id)

    handler = _TOOL_HANDLERS.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        # Inject space_id for tools that accept it
        import inspect
        sig = inspect.signature(handler)
        if "space_id" in sig.parameters:
            arguments["space_id"] = space_id

        result = await handler(**arguments)
        return json.dumps(result, default=str)
    except Exception as e:
        log.error("tool_execution_failed", tool=name, error=str(e))
        return json.dumps({"error": f"Tool '{name}' failed: {str(e)}"})
