"""OpenAI function-calling tool definitions for Laya chat."""

from __future__ import annotations


def get_all_tool_definitions() -> list[dict]:
    """Return all tool definitions in OpenAI function calling format."""
    return [
        *_read_tools(),
        *_write_tools(),
    ]


def _read_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_cards",
                "description": (
                    "Search action cards by keyword, status, priority, or category. "
                    "Use this to find cards matching the user's query."
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
                                "pending", "ready", "requires_approval", "done",
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
                            "description": "Max results to return (default 10).",
                            "default": 10,
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
                    "Use this to find events from Jira, Slack, Gmail, Bitbucket, Calendar, etc."
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
                            "description": "Max results to return (default 10).",
                            "default": 10,
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
                    "Use this to find entity correlations across platforms."
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
                            "description": "Max results to return (default 10).",
                            "default": 10,
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
                    "Use when the user asks about what's new or recent activity."
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
                            "description": "Max results per category (default 10).",
                            "default": 10,
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
                "name": "approve_card",
                "description": "Approve an action card for execution. Use when the user explicitly approves or greenlights a card.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_id": {
                            "type": "string",
                            "description": "The card ID to approve.",
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
