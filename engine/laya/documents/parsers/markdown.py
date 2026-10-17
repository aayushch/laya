# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Markdown parser: one unit per heading section, the heading carried on each."""

from __future__ import annotations

import re
from pathlib import Path

from laya.documents.parsers.base import DocumentParser, ParsedDocument, TextUnit, drop_empty

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def split_markdown(text: str) -> tuple[list[TextUnit], str | None]:
    """Split Markdown into heading-tagged units. Returns ``(units, first_h1)``.

    Each ATX heading starts a new section; the section's body becomes one unit
    tagged with the heading text. Text before the first heading has no heading.
    Fenced code blocks are kept verbatim and a ``#`` inside them is not a heading.
    """
    units: list[TextUnit] = []
    title: str | None = None
    heading: str | None = None
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            units.append(TextUnit(text=body, heading=heading))
        buf.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _ATX_HEADING.match(line)
        if m:
            flush()
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1 and title is None:
                title = heading
            continue
        buf.append(line)
    flush()
    return drop_empty(units), title


class MarkdownParser(DocumentParser):
    name = "md"
    extensions = frozenset({"md", "markdown"})
    mime_types = frozenset({"text/markdown"})

    def parse(self, path: Path) -> ParsedDocument:
        text = path.read_text(encoding="utf-8", errors="replace")
        units, title = split_markdown(text)
        return ParsedDocument(units=units, title=title)


PARSER = MarkdownParser()
