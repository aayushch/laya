# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Corpus document parser registry.

Each ``parsers/<format>.py`` defines one ``DocumentParser`` subclass (see
``base.py``) and exposes it as a module-level singleton ``PARSER``. This
module maps file extensions to those singletons. To add a format, write one
file and add its ``PARSER`` to ``_REGISTRY``.
"""

from __future__ import annotations

from laya.documents.parsers import csv_, docx, html, json_, markdown, pdf, text
from laya.documents.parsers.base import (
    DocumentParser,
    EncryptedDocument,
    NoExtractableText,
    ParsedDocument,
    ParseError,
    TextUnit,
    UnsupportedFormat,
)

# Format name -> parser, in display order.
_REGISTRY: dict[str, DocumentParser] = {
    "pdf": pdf.PARSER,
    "docx": docx.PARSER,
    "md": markdown.PARSER,
    "txt": text.PARSER,
    "csv": csv_.PARSER,
    "json": json_.PARSER,
    "html": html.PARSER,
}

_BY_EXTENSION: dict[str, DocumentParser] = {}
for _parser in _REGISTRY.values():
    for _ext in _parser.extensions:
        if _ext in _BY_EXTENSION:
            raise RuntimeError(f"document parser extension registered twice: {_ext}")
        _BY_EXTENSION[_ext] = _parser


def registry_parsers() -> dict[str, DocumentParser]:
    """Format name -> parser singleton."""
    return dict(_REGISTRY)


def supported_extensions() -> list[str]:
    """Every extension a registered parser handles, lower-case, without the dot."""
    return sorted(_BY_EXTENSION)


def for_extension(ext: str) -> DocumentParser | None:
    """Parser for an extension (with or without a leading dot), or None."""
    return _BY_EXTENSION.get(ext.lower().lstrip("."))


def file_type_for(ext: str) -> str | None:
    """Canonical format name (``documents.file_type``) for an extension, or None."""
    parser = for_extension(ext)
    return parser.name if parser else None


__all__ = [
    "DocumentParser",
    "EncryptedDocument",
    "NoExtractableText",
    "ParsedDocument",
    "ParseError",
    "TextUnit",
    "UnsupportedFormat",
    "file_type_for",
    "for_extension",
    "registry_parsers",
    "supported_extensions",
]
