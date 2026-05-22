# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OpenAI function-calling tool definitions for Laya chat."""

from __future__ import annotations

from laya.egress.tools import get_egress_tool_definitions


def get_all_tool_definitions() -> list[dict]:
    """Return all tool definitions in OpenAI function calling format."""
    return [
        *_read_tools(),
        *_write_tools(),
        *_settings_read_tools(),
        *_settings_write_tools(),
        *get_egress_tool_definitions(),
    ]


# Public helpers that derive tool-name sets dynamically from the group functions
# above. MCP scope filtering uses these so new tools added to any group are
# automatically picked up — never hardcode names elsewhere.

def _names_of(defs: list[dict]) -> set[str]:
    return {d["function"]["name"] for d in defs}


def read_tool_names() -> set[str]:
    """Names of read-only data tools (search/fetch cards, events, entities) and
    the read-only settings introspection tool."""
    return _names_of(_read_tools()) | _names_of(_settings_read_tools())


def write_tool_names() -> set[str]:
    """Names of mutating tools: card lifecycle changes and settings updates."""
    return _names_of(_write_tools()) | _names_of(_settings_write_tools())


def egress_tool_names() -> set[str]:
    """Names of outbound-action tools (Slack/Jira/GitHub/... egress)."""
    return _names_of(get_egress_tool_definitions())


def _read_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_cards",
                "description": (
                    "Search action cards by keyword, status, priority, or category. "
                    "Returns paginated results — check 'has_more' and use 'offset' to retrieve additional pages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text search query to match against card header, summary, and intelligence.",
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "pending", "ready", "done",
                                "failed", "dismissed", "archived", "agent_running",
                                "awaiting_input",
                            ],
                            "description": "Filter by card status.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                            "description": "Filter by priority level.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 20, max 200).",
                            "default": 20,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting position for pagination (default 0). Use with 'total' and 'has_more' from results to page through all matches.",
                            "default": 0,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_card",
                "description": "Get full details of a specific action card by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID (e.g. 'card_abc123').",
                        },
                    },
                    "required": ["card_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_events",
                "description": (
                    "Search raw events by keyword, platform, or actor. "
                    "Returns paginated results — check 'has_more' and use 'offset' to retrieve additional pages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text search query to match against subject and content.",
                        },
                        "platform": {
                            "type": "string",
                            "description": "Filter by source platform (e.g. 'jira', 'slack', 'gmail', 'bitbucket', 'calendar').",
                        },
                        "actor": {
                            "type": "string",
                            "description": "Filter by actor name or email (partial match).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 20, max 200).",
                            "default": 20,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting position for pagination (default 0). Use with 'total' and 'has_more' from results to page through all matches.",
                            "default": 0,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_event",
                "description": "Get full details of a specific event by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "The event ID.",
                        },
                    },
                    "required": ["event_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_entities",
                "description": (
                    "Search cross-platform entities (people, projects, tickets, repos, threads). "
                    "Returns paginated results — check 'has_more' and use 'offset' to retrieve additional pages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for entity name.",
                        },
                        "entity_type": {
                            "type": "string",
                            "enum": ["person", "project", "ticket", "repo", "thread", "issue"],
                            "description": "Filter by entity type.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 10, max 200).",
                            "default": 10,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting position for pagination (default 0). Use with 'total' and 'has_more' from results to page through all matches.",
                            "default": 0,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_entity",
                "description": "Get full details of a specific entity, including all platform references and related cards.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The entity ID.",
                        },
                    },
                    "required": ["entity_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_card_stats",
                "description": (
                    "Get summary statistics about action cards: counts by status, priority, "
                    "platform, and recent activity. Use for overview/dashboard questions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_activity",
                "description": (
                    "Get the most recent events and cards across all platforms. "
                    "Returns paginated results — check 'has_more_events'/'has_more_cards' and use 'offset' to page."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hours": {
                            "type": "integer",
                            "description": "Look back this many hours (default 24).",
                            "default": 24,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results per category (default 10, max 200).",
                            "default": 10,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting position for pagination (default 0).",
                            "default": 0,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_cards_for_event",
                "description": "Get all action cards that were generated from a specific event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "The event ID to find cards for.",
                        },
                    },
                    "required": ["event_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_cards_by_entity",
                "description": (
                    "Get all action cards belonging to a specific entity_id. "
                    "Returns paginated results — check 'has_more' and use 'offset' to retrieve additional pages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": (
                                "The entity ID to look up cards for. "
                                "Format: 'platform:subject_type:subject_id' "
                                "(e.g., 'bitbucket:pullrequest:repo/123')."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 25, max 200).",
                            "default": 25,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting position for pagination (default 0). Use with 'total' and 'has_more' from results to page through all matches.",
                            "default": 0,
                        },
                    },
                    "required": ["entity_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "semantic_search",
                "description": (
                    "Perform a semantic (meaning-based) search across all stored content. "
                    "Use this when keyword search isn't enough and you need conceptual matching."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query.",
                        },
                        "n_results": {
                            "type": "integer",
                            "description": "Number of results (default 10).",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def _write_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "dismiss_card",
                "description": "Dismiss an action card (mark it as not needing action). Use when the user says to dismiss, ignore, or skip a card.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID to dismiss.",
                        },
                    },
                    "required": ["card_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "archive_card",
                "description": "Archive an action card. Use when the user wants to archive a completed or irrelevant card.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID to archive.",
                        },
                    },
                    "required": ["card_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mark_card_done",
                "description": "Mark an action card as done/completed. Use when the user says a card is done, completed, or finished.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID to mark as done.",
                        },
                    },
                    "required": ["card_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reopen_card",
                "description": "Reopen a dismissed or archived card back to pending status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID to reopen.",
                        },
                    },
                    "required": ["card_id"],
                },
            },
        },
    ]


def _settings_read_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_settings",
                "description": (
                    "Read current app settings. Use this before making changes so you "
                    "can report what the old value was, or when the user asks what a "
                    "setting is currently set to."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "enum": [
                                "appearance",
                                "retention",
                                "briefing",
                                "notifications",
                                "feed_preferences",
                                "agent",
                            ],
                            "description": (
                                "Settings section to retrieve. Omit to return all sections."
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },
    ]


def _settings_write_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "update_theme",
                "description": (
                    "Switch the UI between dark and light mode. "
                    "The change takes effect immediately in any open browser tab."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "theme": {
                            "type": "string",
                            "enum": ["dark", "light"],
                            "description": "The theme to apply.",
                        },
                    },
                    "required": ["theme"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_retention",
                "description": (
                    "Change how long action cards and chat history are kept before "
                    "being automatically deleted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_retention_days": {
                            "type": "integer",
                            "description": "Days to retain action cards (1–365).",
                            "minimum": 1,
                            "maximum": 365,
                        },
                        "chat_retention_days": {
                            "type": "integer",
                            "description": "Days to retain chat message history (1–365).",
                            "minimum": 1,
                            "maximum": 365,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_briefing",
                "description": (
                    "Toggle the daily briefing on or off, or change the time and "
                    "timezone it is delivered."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "description": "Enable or disable the daily briefing.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Delivery time in 24-hour HH:MM format (e.g. '07:30').",
                        },
                        "timezone": {
                            "type": "string",
                            "description": (
                                "IANA timezone string for the delivery time "
                                "(e.g. 'America/New_York', 'Europe/London')."
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_notifications",
                "description": (
                    "Toggle notifications on or off, or change the minimum card priority "
                    "that triggers a notification."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "description": "Enable or disable notifications.",
                        },
                        "min_priority": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                            "description": (
                                "Only notify for cards at or above this priority level."
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_feed_preferences",
                "description": (
                    "Change the default feed view: sort order, archived card visibility, "
                    "status/priority filters, or space filter."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sortBy": {
                            "type": "string",
                            "enum": ["newest", "priority", "category", "platform"],
                            "description": "Default sort order for the feed.",
                        },
                        "showArchived": {
                            "type": "boolean",
                            "description": "Whether to show archived cards in the feed.",
                        },
                        "statusFilters": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of card statuses to show. Empty list means show all."
                            ),
                        },
                        "priorityFilters": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of priority levels to show. Empty list means show all."
                            ),
                        },
                        "spaceFilter": {
                            "type": "string",
                            "description": "Space ID to filter the feed by, or null for all spaces.",
                        },
                    },
                    "required": [],
                },
            },
        },
    ]
