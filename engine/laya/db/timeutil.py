# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Canonical timestamp formatting for DB columns.

Every timestamp we store in SQLite must use ONE format so that TEXT range
comparisons and sorts are correct. The canonical form is space-separated naive
UTC (`%Y-%m-%d %H:%M:%S`) — the same shape SQLite's `CURRENT_TIMESTAMP` default
emits, and what the feed's sort/bucket logic already assumes.

Why this module exists: some writers used `datetime.isoformat()`, which produces
a `T`-separated, offset-suffixed string (`2026-07-01T00:00:00+00:00`). Compared
lexicographically against a space-separated column, space (0x20) sorts before
`T` (0x54), so a same-day range silently matched ZERO rows (the "chat can't find
today's cards" bug). Route ALL DB timestamp writes and range-bounds through the
helpers here so that class of bug can't recur.

Do NOT use these for JSON/HTTP payloads, WebSocket messages, or LLM prompt
strings — those keep ISO-8601 (`.isoformat()`). This is strictly the DB storage
format.
"""

from __future__ import annotations

from datetime import datetime, timezone

# The one true on-disk timestamp format (space-separated, naive UTC).
DB_TS_FMT = "%Y-%m-%d %H:%M:%S"


def db_now() -> str:
    """Current UTC time in canonical DB format — for 'now' write stamps."""
    return datetime.now(timezone.utc).strftime(DB_TS_FMT)


def db_ts(dt: datetime) -> str:
    """Normalize any datetime to the canonical DB string (UTC).

    A naive datetime is treated as already-UTC (matching how ingestion
    normalizes upstream event times); an aware one is converted to UTC. The
    instant is preserved — only the rendering changes.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(DB_TS_FMT)


def db_ts_from_epoch(ts: float) -> str:
    """Convert a Unix epoch (seconds) to the canonical DB string (UTC).

    Used to build range-query bounds so they lexicographically match stored
    values instead of `.isoformat()` (which adds a `T` and a `+00:00` offset).
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(DB_TS_FMT)
