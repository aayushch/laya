"""Shared utilities for LLM prompt templates."""

from datetime import datetime, timezone


def current_timestamp_line() -> str:
    """Return a formatted current-date/time line for injection into prompts."""
    now = datetime.now(timezone.utc)
    return f"Current date/time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC ({now.strftime('%A')})"
