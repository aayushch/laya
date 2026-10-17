# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""JSON parser: pretty-printed, split by top-level key or in slices of 50 items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from laya.documents.parsers.base import DocumentParser, ParseError, ParsedDocument, TextUnit, drop_empty

_ITEMS_PER_UNIT = 50


class JsonParser(DocumentParser):
    name = "json"
    extensions = frozenset({"json"})
    mime_types = frozenset({"application/json"})

    def parse(self, path: Path) -> ParsedDocument:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON: {e.msg} at line {e.lineno}") from e

        units: list[TextUnit] = []
        if isinstance(data, dict):
            for key, value in data.items():
                units.append(TextUnit(text=json.dumps(value, indent=2, ensure_ascii=False), heading=str(key)))
        elif isinstance(data, list):
            for start in range(0, len(data), _ITEMS_PER_UNIT):
                end = min(start + _ITEMS_PER_UNIT, len(data))
                units.append(TextUnit(
                    text=json.dumps(data[start:end], indent=2, ensure_ascii=False),
                    heading=f"items {start}-{end - 1}",
                ))
        else:
            units.append(TextUnit(text=json.dumps(data, indent=2, ensure_ascii=False)))
        return ParsedDocument(units=drop_empty(units))


PARSER = JsonParser()
