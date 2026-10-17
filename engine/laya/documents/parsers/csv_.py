# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""CSV parser: rows rendered as ``col: val | col: val`` lines, 50 rows per unit."""

from __future__ import annotations

import csv
from pathlib import Path

from laya.documents.parsers.base import DocumentParser, NoExtractableText, ParsedDocument, TextUnit, drop_empty

_ROWS_PER_UNIT = 50


class CsvParser(DocumentParser):
    name = "csv"
    extensions = frozenset({"csv", "tsv"})
    mime_types = frozenset({"text/csv", "text/tab-separated-values"})

    def parse(self, path: Path) -> ParsedDocument:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            raise NoExtractableText("The CSV file is empty")
        header = [h.strip() or f"col{i + 1}" for i, h in enumerate(rows[0])]
        body = rows[1:]
        heading = path.stem
        units: list[TextUnit] = []
        if not body:
            units.append(TextUnit(text="Columns: " + ", ".join(header), heading=heading))
        for start in range(0, len(body), _ROWS_PER_UNIT):
            lines = []
            for row in body[start:start + _ROWS_PER_UNIT]:
                cells = [
                    f"{header[i] if i < len(header) else f'col{i + 1}'}: {val.strip()}"
                    for i, val in enumerate(row)
                    if val.strip()
                ]
                if cells:
                    lines.append(" | ".join(cells))
            if lines:
                units.append(TextUnit(text="\n".join(lines), heading=heading))
        return ParsedDocument(units=drop_empty(units), metadata={"rows": len(body), "columns": header})


PARSER = CsvParser()
