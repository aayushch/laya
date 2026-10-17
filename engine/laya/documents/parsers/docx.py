# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""DOCX parser: mammoth converts to Markdown, then the Markdown splitter
assigns headings. Images are dropped and tables arrive as paragraphs."""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from laya.documents.parsers.base import DocumentParser, NoExtractableText, ParseError, ParsedDocument
from laya.documents.parsers.markdown import split_markdown

log = structlog.get_logger()

# mammoth escapes Markdown punctuation in prose ("PAY\-42", "\*"); the text is
# indexed, not rendered, so the escapes are removed to keep identifiers intact.
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


class DocxParser(DocumentParser):
    name = "docx"
    extensions = frozenset({"docx"})
    mime_types = frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"})

    def parse(self, path: Path) -> ParsedDocument:
        import mammoth

        try:
            with path.open("rb") as f:
                result = mammoth.convert_to_markdown(f)
        except Exception as e:
            raise ParseError(f"Could not read DOCX: {e}") from e
        for message in result.messages:
            log.debug("docx_parse_message", path=str(path), message=str(message))
        markdown = _MD_ESCAPE.sub(r"\1", result.value or "")
        units, title = split_markdown(markdown)
        if not units:
            raise NoExtractableText("The document contains no text")
        return ParsedDocument(units=units, title=title)


PARSER = DocxParser()
