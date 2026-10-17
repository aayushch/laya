# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Map-reduce summary of a document at ingest.

Chunks are packed into windows of ``settings.documents.summary_window_chars``
characters; each window is summarised (map, at most two in flight), and the
window summaries are merged (reduce), hierarchically in groups of eight when
there are too many to merge in one call. Very long documents are sampled: the
first 12, the last 6 and 6 evenly spaced windows in between, and the summary
says so.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from laya.config import DEFAULT_SETTINGS, load_settings
from laya.llm.client import llm_call
from laya.llm.prompts.document_summary import (
    build_document_map_messages,
    build_document_reduce_messages,
    get_document_map_schema,
    get_document_reduce_schema,
)

log = structlog.get_logger()

_MAP_CONCURRENCY = 2
_REDUCE_GROUP = 8
_SAMPLE_HEAD, _SAMPLE_TAIL, _SAMPLE_MIDDLE = 12, 6, 6


def summary_role() -> str:
    """``document_summary`` when the user configured a model for it, else
    ``stager`` so the per-space stager override applies."""
    models = load_settings().get("models", {})
    return "document_summary" if models.get("document_summary") else "stager"


def _settings() -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["documents"]
    cfg = load_settings().get("documents", {})
    return {
        "window_chars": int(cfg.get("summary_window_chars", defaults["summary_window_chars"])),
        "max_windows": int(cfg.get("summary_max_windows", defaults["summary_max_windows"])),
    }


def pack_windows(chunks: list[dict[str, Any]], window_chars: int) -> list[dict[str, Any]]:
    """Group consecutive chunks into windows of at most ``window_chars``.

    Each window is ``{"text", "page_range"}``; a chunk longer than the window
    stands alone.
    """
    windows: list[dict[str, Any]] = []
    buf: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        if not buf:
            return
        pages = [c["page_start"] for c in buf if c.get("page_start")] + [c["page_end"] for c in buf if c.get("page_end")]
        page_range = f"{min(pages)}-{max(pages)}" if pages else None
        windows.append({"text": "\n\n".join(c["text"] for c in buf), "page_range": page_range})
        buf.clear()

    for chunk in chunks:
        clen = len(chunk["text"])
        if buf and size + clen > window_chars:
            flush()
            size = 0
        buf.append(chunk)
        size += clen
    flush()
    return windows


def sample_windows(windows: list[dict[str, Any]], max_windows: int) -> tuple[list[dict[str, Any]], str | None]:
    """Cap the windows at ``max_windows``: keep the head, the tail and an even
    sample of the middle. Returns the sample and a note for the reduce prompt."""
    if len(windows) <= max_windows:
        return windows, None
    head = windows[:_SAMPLE_HEAD]
    tail = windows[-_SAMPLE_TAIL:]
    middle = windows[_SAMPLE_HEAD:-_SAMPLE_TAIL]
    picked: list[dict[str, Any]] = []
    if middle and _SAMPLE_MIDDLE:
        step = len(middle) / _SAMPLE_MIDDLE
        picked = [middle[int(i * step)] for i in range(_SAMPLE_MIDDLE)]
    sample = head + picked + tail
    note = f"summary covers a sample of {len(sample)} of {len(windows)} sections"
    return sample, note


async def summarize_document(
    document_id: str,
    chunks: list[dict[str, Any]],
    title: str,
    file_type: str,
    page_count: int | None,
    space_id: str | None,
) -> dict[str, Any]:
    """Return ``{"summary", "key_points", "identifiers", "model"}`` for the document."""
    cfg = _settings()
    windows = pack_windows(chunks, cfg["window_chars"])
    windows, note = sample_windows(windows, cfg["max_windows"])
    role = summary_role()
    if not windows:
        return {"summary": "", "key_points": [], "identifiers": [], "model": None}

    # Single window: one reduce-style call straight from the text.
    if len(windows) == 1:
        partial = {"summary": windows[0]["text"], "key_points": [], "identifiers": [], "page_range": windows[0]["page_range"]}
        return await _reduce(role, title, file_type, page_count, [partial], note, space_id)

    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map(i: int, window: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            response = await llm_call(
                role=role,
                messages=build_document_map_messages(title, file_type, i, len(windows), window["text"], window["page_range"]),
                response_schema=get_document_map_schema(),
                step="document_summary_map",
                temperature=0.2,
                max_tokens=1024,
                space_id=space_id,
            )
        parsed = response.parsed or {}
        return {
            "summary": parsed.get("summary") or response.content or "",
            "key_points": list(parsed.get("key_points") or []),
            "identifiers": list(parsed.get("identifiers") or []),
            "people": list(parsed.get("people") or []),
            "page_range": window["page_range"],
        }

    partials = list(await asyncio.gather(*(_map(i, w) for i, w in enumerate(windows))))

    # Hierarchical reduce: merge groups of eight until one call can hold them.
    while len(partials) > _REDUCE_GROUP:
        groups = [partials[i:i + _REDUCE_GROUP] for i in range(0, len(partials), _REDUCE_GROUP)]
        merged: list[dict[str, Any]] = []
        for group in groups:
            r = await _reduce(role, title, file_type, page_count, group, None, space_id)
            merged.append({
                "summary": r["summary"], "key_points": r["key_points"], "identifiers": r["identifiers"],
                "page_range": _span(group),
            })
        partials = merged

    return await _reduce(role, title, file_type, page_count, partials, note, space_id)


def _span(group: list[dict[str, Any]]) -> str | None:
    ranges = [g["page_range"] for g in group if g.get("page_range")]
    if not ranges:
        return None
    return f"{ranges[0].split('-')[0]}-{ranges[-1].split('-')[-1]}"


async def _reduce(
    role: str,
    title: str,
    file_type: str,
    page_count: int | None,
    partials: list[dict[str, Any]],
    note: str | None,
    space_id: str | None,
) -> dict[str, Any]:
    response = await llm_call(
        role=role,
        messages=build_document_reduce_messages(title, file_type, page_count, partials, note),
        response_schema=get_document_reduce_schema(),
        step="document_summary_reduce",
        temperature=0.2,
        max_tokens=2048,
        space_id=space_id,
    )
    parsed = response.parsed or {}
    summary = (parsed.get("summary") or response.content or "").strip()
    if note and note not in summary:
        summary = f"{summary}\n\n({note})"
    return {
        "summary": summary,
        "key_points": list(parsed.get("key_points") or []),
        "identifiers": list(parsed.get("identifiers") or []),
        "model": response.model,
    }
