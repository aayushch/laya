# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Centralized search limit constants for chat tools and coherence pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

# Chat tool limits (per-call max and default)
CHAT_SEARCH_MAX = 200
CHAT_SEARCH_DEFAULT = 20

SEMANTIC_SEARCH_MAX = 200
SEMANTIC_SEARCH_DEFAULT = 10

ENTITY_SEARCH_MAX = 200
ENTITY_SEARCH_DEFAULT = 10

CARDS_BY_ENTITY_MAX = 200
CARDS_BY_ENTITY_DEFAULT = 25

RECENT_ACTIVITY_MAX = 200
RECENT_ACTIVITY_DEFAULT = 10

CONTACT_SEARCH_MAX = 20

# Coherence / trace pipeline
TRACE_TEXT_SEARCH_MAX = 200
TRACE_IDENTIFIER_SEARCH_MAX = 30
TRACE_SEMANTIC_SEARCH_MAX = 30
TRACE_ENTITY_SEARCH_MAX = 20
TRACE_FUZZY_SEARCH_MAX = 30
TRACE_EVENT_SEARCH_MAX = 20


def parse_iso_to_timestamp(value: str | None) -> float | None:
    """Parse an ISO 8601 date/datetime string to a Unix timestamp.

    Accepts date-only ('2026-04-01') or full datetime with optional timezone.
    Returns None if the value is None or unparseable — never raises.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None
