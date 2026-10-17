# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for sentence chunking (db/chunking) and page/heading-aware document
chunking (documents/chunking)."""

import random
import re

from laya.db.chunking import chunk_segments, chunk_text
from laya.documents.chunking import chunk_units
from laya.documents.parsers.base import TextUnit


def _prose(seed: int, sentences: int) -> str:
    rnd = random.Random(seed)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    out = []
    for _ in range(sentences):
        n = rnd.randint(3, 25)
        out.append(rnd.choice(words).capitalize() + " " + " ".join(rnd.choices(words, k=n)) + rnd.choice(".!?"))
    return " ".join(out)


# The sentence packing as it was before chunk_segments existed; chunk_text must
# keep producing exactly this.
_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z"])|(?:\n\s*\n)')


def _legacy_chunk_text(text, target_chars=1400, overlap_chars=200):
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= int(target_chars * 1.2):
        return [text]
    sentences = [s.strip() for s in _BOUNDARY.split(text) if s.strip()]
    if not sentences:
        return [text]
    chunks, parts, length = [], [], 0
    for s in sentences:
        if length + len(s) > target_chars and parts:
            chunks.append(" ".join(parts))
            overlap, olen = [], 0
            for prev in reversed(parts):
                if olen + len(prev) > overlap_chars:
                    break
                overlap.insert(0, prev)
                olen += len(prev)
            parts, length = overlap, olen
        parts.append(s)
        length += len(s)
    if parts:
        final = " ".join(parts)
        if not chunks or final != chunks[-1]:
            chunks.append(final)
    return chunks or [text]


class TestChunkTextGolden:
    def test_matches_legacy_on_varied_inputs(self):
        samples = ["", "   ", "Short text.", "x" * 5000, "para\n\npara two"]
        samples += [_prose(i, 120) for i in range(12)]
        for s in samples:
            assert chunk_text(s) == _legacy_chunk_text(s)

    def test_no_empty_chunks_on_long_text(self):
        for c in chunk_text(_prose(3, 200)):
            assert c.strip()


class TestChunkSegments:
    def test_single_untagged_segment_equals_chunk_text(self):
        text = _prose(7, 150)
        assert [c for c, _ in chunk_segments([(text, None)])] == chunk_text(text)

    def test_tags_follow_sentences(self):
        segs = [(_prose(1, 60), 1), (_prose(2, 60), 2), (_prose(3, 60), 3)]
        chunks = chunk_segments(segs)
        assert len(chunks) > 1
        for text, tags in chunks:
            assert tags == sorted(set(tags))  # distinct, in order
            assert all(t in (1, 2, 3) for t in tags)
        # The first chunk comes from page 1 only; the last from page 3.
        assert chunks[0][1][0] == 1
        assert chunks[-1][1][-1] == 3

    def test_short_input_single_chunk_lists_all_tags(self):
        chunks = chunk_segments([("One. ", "a"), ("Two.", "b")])
        assert len(chunks) == 1
        assert chunks[0][1] == ["a", "b"]


class TestChunkUnits:
    def test_page_range_and_heading(self):
        units = [TextUnit(text=_prose(i, 40), page=i, heading="Intro") for i in range(1, 6)]
        chunks = chunk_units(units)
        assert chunks[0].page_start == 1
        assert chunks[-1].page_end == 5
        for i, c in enumerate(chunks):
            assert c.chunk_index == i
            assert c.heading == "Intro"
            assert c.text.startswith("Intro\n")
            assert c.page_start <= c.page_end
            assert c.char_count == len(c.text)

    def test_chunks_do_not_cross_sections(self):
        units = [
            TextUnit(text=_prose(1, 50), heading="A"),
            TextUnit(text=_prose(2, 50), heading="B"),
        ]
        chunks = chunk_units(units)
        headings = [c.heading for c in chunks]
        assert "A" in headings and "B" in headings
        # Once B starts, A never reappears.
        first_b = headings.index("B")
        assert all(h == "B" for h in headings[first_b:])

    def test_tiny_section_merges_forward(self):
        units = [
            TextUnit(text="Tiny.", heading="Preface"),
            TextUnit(text=_prose(4, 30), heading="Body"),
        ]
        chunks = chunk_units(units, min_section_chars=300)
        assert all(c.heading == "Body" for c in chunks)
        assert "Tiny." in chunks[0].text

    def test_no_heading_no_prefix(self):
        chunks = chunk_units([TextUnit(text="Plain paragraph.")])
        assert chunks[0].text == "Plain paragraph."
        assert chunks[0].heading is None
        assert chunks[0].page_start is None

    def test_empty_units_yield_nothing(self):
        assert chunk_units([]) == []
