# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Abstract base for Corpus document parsers.

Each ``parsers/<format>.py`` defines one ``DocumentParser`` subclass and
exposes it as a module-level singleton ``PARSER = XParser()``, the same shape
as the egress platform adapters (``egress/platforms/base.py``). A subclass
that forgets a required member fails at instantiation (import time), not at
parse time.

A parser turns a file on disk into a flat list of ``TextUnit`` values. A unit
is the smallest piece of text that carries a location (a page number, the
nearest heading); ``documents/chunking.py`` packs units into embedding-sized
chunks without losing those locations.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TextUnit:
    """A run of extracted text with the location it came from."""

    text: str
    page: int | None = None      # 1-based page for paged formats (PDF); None otherwise
    heading: str | None = None   # nearest enclosing heading, when the format has headings


@dataclass
class ParsedDocument:
    units: list[TextUnit]
    page_count: int | None = None
    title: str | None = None     # from file metadata or the first H1, if any
    metadata: dict[str, Any] = field(default_factory=dict)


class ParseError(Exception):
    """Base class for parse failures. ``error_code`` is stored on the document row."""

    error_code = "parse_error"


class NoExtractableText(ParseError):
    """The file holds no text to index (a scanned PDF, an image-only document)."""

    error_code = "no_text"


class EncryptedDocument(ParseError):
    """The file is password-protected and cannot be opened without a password."""

    error_code = "encrypted"


class UnsupportedFormat(ParseError):
    """The file's format is not one a registered parser handles."""

    error_code = "unsupported"


class DocumentParser(abc.ABC):
    """One format's parser. ``parse`` is synchronous; ingest runs it in a thread."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short format name, e.g. ``"pdf"``."""

    @property
    @abc.abstractmethod
    def extensions(self) -> frozenset[str]:
        """Lower-case file extensions without the dot that this parser handles."""

    mime_types: frozenset[str] = frozenset()

    @abc.abstractmethod
    def parse(self, path: Path) -> ParsedDocument:
        """Extract text units from the file at ``path``. Raises ``ParseError``."""


_SPACE_RUN = re.compile(r"[ \t\f\v]+")
_BLANK_RUN = re.compile(r"\n\s*\n+")


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces and tabs, keep paragraph breaks as one blank line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def drop_empty(units: list[TextUnit]) -> list[TextUnit]:
    """Normalise each unit's text and drop the ones with nothing left."""
    out: list[TextUnit] = []
    for u in units:
        cleaned = normalize_whitespace(u.text)
        if cleaned:
            out.append(TextUnit(text=cleaned, page=u.page, heading=u.heading))
    return out
