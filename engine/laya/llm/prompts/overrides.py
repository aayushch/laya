# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Custom prompt override loader.

Users can drop markdown/text files into ~/.laya/prompts/ to replace any
hardcoded system prompt.  The engine never creates files in that directory —
if a file is missing or deleted, the hardcoded default in the Python module
is used.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

LAYA_PROMPTS_DIR = Path.home() / ".laya" / "prompts"

PROMPT_FILES: dict[str, str] = {
    "router": "router.md",
    "stager": "stager.md",
    "omni": "omni.md",
    "group_summary_initial": "group_summary_initial.md",
    "group_summary_rolling": "group_summary_rolling.md",
    "briefing": "briefing.md",
    "summarizer": "summarizer.md",
    "summarizer_status_change": "summarizer_status_change.md",
    "engineer": "engineer.md",
    "comms": "comms.md",
    "sales": "sales.md",
    "hr": "hr.md",
    "ops": "ops.md",
    "finance": "finance.md",
    "chat": "chat.md",
    "chat_title": "chat_title.md",
    "chat_polish": "chat_polish.md",
    "learner": "learner.md",
    "context_learner": "context_learner.md",
    "trace_narrative": "trace_narrative.md",
    "trace_summary": "trace_summary.md",
    "trace_filter": "trace_filter.md",
}

_FILENAME_TO_KEY = {v: k for k, v in PROMPT_FILES.items()}

_overrides: dict[str, str] = {}


def load_custom_prompts() -> dict[str, str]:
    """Scan ~/.laya/prompts/ and load recognized files into the override store.

    Returns the dict of loaded overrides (key → content) for logging/API use.
    Clears previous overrides so this doubles as a reload function.
    """
    _overrides.clear()

    if not LAYA_PROMPTS_DIR.is_dir():
        log.debug("custom_prompts_dir_not_found", path=str(LAYA_PROMPTS_DIR))
        return {}

    unrecognized: list[str] = []

    for entry in LAYA_PROMPTS_DIR.iterdir():
        if not entry.is_file():
            continue

        key = _FILENAME_TO_KEY.get(entry.name)
        if key is None:
            unrecognized.append(entry.name)
            continue

        try:
            content = entry.read_text(encoding="utf-8").strip()
        except Exception as exc:
            log.warning("custom_prompt_read_error", file=entry.name, error=str(exc))
            continue

        if not content:
            log.warning("custom_prompt_empty", file=entry.name, key=key)
            continue

        _overrides[key] = content

    if _overrides:
        log.info("custom_prompts_loaded", keys=sorted(_overrides.keys()))
    if unrecognized:
        log.warning(
            "custom_prompts_unrecognized_files",
            files=sorted(unrecognized),
            recognized=sorted(PROMPT_FILES.values()),
        )

    return dict(_overrides)


def get_prompt(key: str, default: str) -> str:
    """Return the custom prompt for *key* if one was loaded, otherwise *default*."""
    return _overrides.get(key, default)


def get_override_status() -> list[dict]:
    """Return override status for every known prompt key (for diagnostics API)."""
    return [
        {
            "key": key,
            "file": filename,
            "overridden": key in _overrides,
        }
        for key, filename in PROMPT_FILES.items()
    ]
