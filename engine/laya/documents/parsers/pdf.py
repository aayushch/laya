# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""PDF parser on pypdf: one unit per page; scanned PDFs are rejected."""

from __future__ import annotations

from pathlib import Path

from laya.documents.parsers.base import (
    DocumentParser,
    EncryptedDocument,
    NoExtractableText,
    ParseError,
    ParsedDocument,
    TextUnit,
    drop_empty,
)


def _min_text_chars(page_count: int) -> int:
    """Below this many characters across the whole file the PDF is treated as
    scanned (image-only): a few stray characters per page is noise, not text."""
    return max(40, 25 * page_count)


class PdfParser(DocumentParser):
    name = "pdf"
    extensions = frozenset({"pdf"})
    mime_types = frozenset({"application/pdf"})

    def parse(self, path: Path) -> ParsedDocument:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                # An empty user password opens owner-locked files; anything
                # else needs a password the user never gave us.
                if reader.decrypt("") == 0:
                    raise EncryptedDocument("The PDF is password-protected")
            page_count = len(reader.pages)
            units: list[TextUnit] = []
            total = 0
            for i, page in enumerate(reader.pages):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                total += len(text.strip())
                units.append(TextUnit(text=text, page=i + 1))
            title = None
            try:
                meta_title = reader.metadata.title if reader.metadata else None
                if meta_title and str(meta_title).strip():
                    title = str(meta_title).strip()
            except Exception:
                title = None
        except ParseError:
            raise
        except PdfReadError as e:
            raise ParseError(f"Could not read PDF: {e}") from e
        except Exception as e:
            raise ParseError(f"Could not read PDF: {e}") from e

        if total < _min_text_chars(page_count):
            raise NoExtractableText(
                "No extractable text; this looks like a scanned PDF (OCR is not supported)"
            )
        return ParsedDocument(units=drop_empty(units), page_count=page_count, title=title)


PARSER = PdfParser()
