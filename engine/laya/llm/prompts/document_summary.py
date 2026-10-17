# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Prompt templates for the Corpus document summary (map-reduce at ingest)."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts.overrides import get_prompt

DOCUMENT_MAP_SYSTEM_PROMPT = """\
You summarise ONE section of a longer document for a professional work assistant.
The section is one of several; another step merges the section summaries later.

Return a JSON object with:
- **summary**: 2-4 sentences covering what this section says. Plain prose.
- **key_points**: up to 6 short bullet strings with the facts, numbers, dates, \
decisions and open questions in the section.
- **identifiers**: work-item identifiers mentioned (ticket keys like PROJ-123, \
PR numbers like PR #540, document or release names). Empty list if none.
- **people**: people named in the section. Empty list if none.

Rules:
- Only report what the section states; never add outside knowledge.
- Keep names, numbers, dates and identifiers exactly as written.
- Never use emoji or icon characters anywhere in your output.
"""

DOCUMENT_REDUCE_SYSTEM_PROMPT = """\
You merge section summaries of ONE document into a single summary for a \
professional work assistant. The reader wants to know what the document is, \
what it concludes and what it asks of them.

Return a JSON object with:
- **summary**: Markdown of at most 300 words with exactly these headings:
  ## Overview — what the document is and who it is for (2-3 sentences)
  ## Key points — the most important facts and conclusions (bullets)
  ## Decisions & action items — decisions recorded and work requested (bullets; \
write "None recorded." if there are none)
  ## Identifiers — the tickets, PRs, releases and names the document references \
(comma-separated; write "None." if there are none)
- **key_points**: the Key points bullets as plain strings (max 8).
- **identifiers**: distinct work-item identifiers across all sections.

Rules:
- Merge, do not concatenate: drop repetition across sections.
- Only use what the section summaries contain.
- Keep names, numbers, dates and identifiers exactly as written.
- Never use emoji or icon characters anywhere in your output.
"""

DOCUMENT_MAP_JSON_SCHEMA: dict[str, Any] = {
    "name": "document_section_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}},
            "identifiers": {"type": "array", "items": {"type": "string"}},
            "people": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "key_points", "identifiers", "people"],
        "additionalProperties": False,
    },
}

DOCUMENT_REDUCE_JSON_SCHEMA: dict[str, Any] = {
    "name": "document_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}},
            "identifiers": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "key_points", "identifiers"],
        "additionalProperties": False,
    },
}


def get_document_map_schema() -> dict[str, Any]:
    return DOCUMENT_MAP_JSON_SCHEMA


def get_document_reduce_schema() -> dict[str, Any]:
    return DOCUMENT_REDUCE_JSON_SCHEMA


def build_document_map_messages(
    title: str,
    file_type: str,
    part_index: int,
    part_total: int,
    text: str,
    page_range: str | None = None,
) -> list[dict[str, str]]:
    """Messages for summarising one window of a document."""
    location = f" (pages {page_range})" if page_range else ""
    user = (
        f"Document: {title} ({file_type.upper()})\n"
        f"Section {part_index + 1} of {part_total}{location}\n\n"
        f"---\n{text}\n---"
    )
    return [
        {"role": "system", "content": get_prompt("document_summary_map", DOCUMENT_MAP_SYSTEM_PROMPT)},
        {"role": "user", "content": user},
    ]


def build_document_reduce_messages(
    title: str,
    file_type: str,
    page_count: int | None,
    partials: list[dict[str, Any]],
    note: str | None = None,
) -> list[dict[str, str]]:
    """Messages for merging section summaries into the document summary."""
    parts: list[str] = []
    for i, p in enumerate(partials):
        block = [f"### Section {i + 1}"]
        if p.get("page_range"):
            block.append(f"Pages: {p['page_range']}")
        block.append(p.get("summary", "").strip())
        points = p.get("key_points") or []
        if points:
            block.append("Key points:\n" + "\n".join(f"- {k}" for k in points))
        idents = p.get("identifiers") or []
        if idents:
            block.append("Identifiers: " + ", ".join(idents))
        people = p.get("people") or []
        if people:
            block.append("People: " + ", ".join(people))
        parts.append("\n".join(block))
    pages = f", {page_count} pages" if page_count else ""
    user = f"Document: {title} ({file_type.upper()}{pages})\n"
    if note:
        user += f"Note: {note}\n"
    user += "\n" + "\n\n".join(parts)
    return [
        {"role": "system", "content": get_prompt("document_summary_reduce", DOCUMENT_REDUCE_SYSTEM_PROMPT)},
        {"role": "user", "content": user},
    ]
