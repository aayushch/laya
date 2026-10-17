# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Corpus document parser registry and each parser."""

import json
from pathlib import Path

import pytest

from laya.documents import parsers
from laya.documents.parsers import (
    EncryptedDocument,
    NoExtractableText,
    for_extension,
    registry_parsers,
    supported_extensions,
)
from laya.documents.parsers.base import DocumentParser, normalize_whitespace

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


class TestRegistryParity:
    def test_every_parser_module_registers_exactly_one_parser(self):
        pkg_dir = Path(parsers.__file__).parent
        modules = sorted(
            p.stem for p in pkg_dir.glob("*.py") if p.stem not in ("__init__", "base")
        )
        registered = {type(p).__module__.rsplit(".", 1)[-1] for p in registry_parsers().values()}
        assert set(modules) == registered

    def test_every_registered_parser_is_a_document_parser(self):
        for name, p in registry_parsers().items():
            assert isinstance(p, DocumentParser)
            assert p.name == name
            assert p.extensions

    def test_extensions_unique_and_sorted(self):
        exts = supported_extensions()
        assert exts == sorted(exts)
        assert len(exts) == len(set(exts))
        assert {"pdf", "docx", "md", "txt", "csv", "json", "html"} <= set(exts)

    def test_for_extension_tolerates_dot_and_case(self):
        assert for_extension(".PDF") is for_extension("pdf")
        assert for_extension("exe") is None


class TestTextParsers:
    def test_markdown_sections_and_title(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("# Title\n\nIntro.\n\n## Part A\n\nBody a.\n\n```\n# not a heading\n```\n\n## Part B\n\nBody b.")
        doc = for_extension("md").parse(f)
        assert doc.title == "Title"
        assert [u.heading for u in doc.units] == ["Title", "Part A", "Part B"]
        assert "# not a heading" in doc.units[1].text

    def test_text_paragraphs(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("one  two\n\nthree\n\n\n\nfour")
        doc = for_extension("txt").parse(f)
        assert [u.text for u in doc.units] == ["one two", "three", "four"]

    def test_csv_rows_rendered(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text("name,age\nbob,3\nann,4\n")
        doc = for_extension("csv").parse(f)
        assert doc.units[0].heading == "a"
        assert "name: bob | age: 3" in doc.units[0].text
        assert doc.metadata["rows"] == 2

    def test_csv_empty_raises(self, tmp_path):
        f = tmp_path / "e.csv"
        f.write_text("\n")
        with pytest.raises(NoExtractableText):
            for_extension("csv").parse(f)

    def test_json_object_split_by_key(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text(json.dumps({"alpha": {"x": 1}, "beta": [1, 2]}))
        doc = for_extension("json").parse(f)
        assert [u.heading for u in doc.units] == ["alpha", "beta"]

    def test_json_array_sliced(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text(json.dumps(list(range(120))))
        doc = for_extension("json").parse(f)
        assert len(doc.units) == 3
        assert doc.units[0].heading == "items 0-49"

    def test_html_headings_title_and_skips(self, tmp_path):
        f = tmp_path / "a.html"
        f.write_text(
            "<html><head><title>Page T</title><style>x{}</style></head><body>"
            "<nav>skip me</nav><h1>H1</h1><p>para 1</p><script>alert(1)</script>"
            "<h2>H2</h2><div>para 2<br>line</div></body></html>"
        )
        doc = for_extension("html").parse(f)
        assert doc.title == "Page T"
        assert [(u.heading, u.text) for u in doc.units] == [("H1", "para 1"), ("H2", "para 2\nline")]
        assert all("skip me" not in u.text and "alert" not in u.text for u in doc.units)

    def test_normalize_whitespace(self):
        assert normalize_whitespace("a \t b\r\n\r\n\n c ") == "a b\n\nc"


class TestPdfParser:
    def test_two_page_fixture(self):
        doc = for_extension("pdf").parse(FIXTURES / "roadmap.pdf")
        assert doc.page_count == 2
        assert [u.page for u in doc.units] == [1, 2]
        assert "PAY-42" in doc.units[0].text
        assert "PAY-77" in doc.units[1].text

    def test_blank_pdf_is_scanned(self, tmp_path):
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        w.add_blank_page(width=200, height=200)
        f = tmp_path / "blank.pdf"
        w.write(str(f))
        with pytest.raises(NoExtractableText):
            for_extension("pdf").parse(f)

    def test_encrypted_pdf(self, tmp_path):
        from pypdf import PdfReader, PdfWriter

        w = PdfWriter(clone_from=PdfReader(str(FIXTURES / "roadmap.pdf")))
        w.encrypt("secret")
        f = tmp_path / "locked.pdf"
        w.write(str(f))
        with pytest.raises(EncryptedDocument):
            for_extension("pdf").parse(f)


class TestDocxParser:
    def test_fixture_headings(self):
        doc = for_extension("docx").parse(FIXTURES / "notes.docx")
        assert doc.title == "Design Review Notes"
        assert [u.heading for u in doc.units] == ["Design Review Notes", "Decisions"]
        # mammoth's Markdown escapes are removed so identifiers stay intact.
        assert "PAY-42" in doc.units[0].text
