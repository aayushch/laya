# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""HTML parser on the stdlib ``html.parser``: block elements become units,
``h1``-``h6`` text becomes the current heading, and script/style/nav content
is dropped."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from laya.documents.parsers.base import DocumentParser, ParsedDocument, TextUnit, drop_empty

_SKIP = {"script", "style", "nav", "noscript", "template", "svg"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = {
    "p", "div", "section", "article", "li", "tr", "blockquote", "pre",
    "table", "ul", "ol", "dl", "dd", "dt", "header", "footer", "main",
    "aside", "figure", "figcaption", "br", "hr",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[TextUnit] = []
        self.title: str | None = None
        self.heading: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._heading_buf: list[str] | None = None
        self._buf: list[str] = []

    def _flush(self) -> None:
        text = "".join(self._buf).strip()
        if text:
            self.units.append(TextUnit(text=text, heading=self.heading))
        self._buf = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self._flush()
            self._heading_buf = []
        elif tag in _BLOCKS:
            if tag in ("br", "hr"):
                self._buf.append("\n")
            else:
                self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADINGS and self._heading_buf is not None:
            heading = " ".join("".join(self._heading_buf).split())
            self._heading_buf = None
            if heading:
                self.heading = heading
                if tag == "h1" and self.title is None:
                    self.title = heading
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            t = " ".join(data.split())
            if t and self.title is None:
                self.title = t
            return
        if self._heading_buf is not None:
            self._heading_buf.append(data)
            return
        self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


class HtmlParser(DocumentParser):
    name = "html"
    extensions = frozenset({"html", "htm", "xhtml"})
    mime_types = frozenset({"text/html", "application/xhtml+xml"})

    def parse(self, path: Path) -> ParsedDocument:
        raw = path.read_text(encoding="utf-8", errors="replace")
        ex = _Extractor()
        ex.feed(raw)
        ex.close()
        return ParsedDocument(units=drop_empty(ex.units), title=ex.title)


PARSER = HtmlParser()
