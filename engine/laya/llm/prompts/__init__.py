# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared utilities for LLM prompt templates."""

from datetime import datetime, timezone
from typing import Any


def current_timestamp_line() -> str:
    """Return a formatted current-date/time line for injection into prompts."""
    now = datetime.now(timezone.utc)
    return f"Current date/time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC ({now.strftime('%A')})"


def summarize_findings(findings: dict[str, Any]) -> str:
    """Summarize a prior worker's findings dict into readable prompt text.

    Shared by the comms/hr/sales drafting prompts, which carried byte-identical
    private copies (review §5.1 — P7-2).
    """
    parts = []
    if "agent_result" in findings:
        parts.append(f"Agent result: {str(findings['agent_result'])[:500]}")
    if "draft" in findings:
        draft = findings["draft"]
        if isinstance(draft, dict):
            parts.append(f"Draft: {draft.get('draft_reply', str(draft))[:500]}")
        else:
            parts.append(f"Draft: {str(draft)[:500]}")
    if not parts:
        parts.append(str(findings)[:500])
    return "\n".join(parts)
