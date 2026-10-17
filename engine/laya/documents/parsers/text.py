# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Plain-text parser: one unit per paragraph (blank-line separated)."""

from __future__ import annotations

import re
from pathlib import Path

from laya.documents.parsers.base import DocumentParser, ParsedDocument, TextUnit, drop_empty

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


class TextParser(DocumentParser):
    name = "txt"
    extensions = frozenset({"txt", "text", "log"})
    mime_types = frozenset({"text/plain"})

    def parse(self, path: Path) -> ParsedDocument:
        text = path.read_text(encoding="utf-8", errors="replace")
        units = [TextUnit(text=p) for p in _PARAGRAPH_BREAK.split(text)]
        return ParsedDocument(units=drop_empty(units))


PARSER = TextParser()
