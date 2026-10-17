# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Page- and heading-aware chunking of parsed documents.

``chunk_units`` packs the ``TextUnit`` list a parser produced into
embedding-sized chunks using the same sentence packing as card text
(``db/chunking.chunk_segments``) while keeping, for every chunk, the page
range it spans and the heading it sits under. Chunks never cross a heading
section, except that a section too small to stand alone is merged forward
into the next one.
"""

from __future__ import annotations

from dataclasses import dataclass

from laya.db.chunking import _CHUNK_OVERLAP_CHARS, _CHUNK_TARGET_CHARS, chunk_segments
from laya.documents.parsers.base import TextUnit


@dataclass
class DocChunk:
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    char_count: int


def chunk_units(
    units: list[TextUnit],
    *,
    target_chars: int = _CHUNK_TARGET_CHARS,
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,
    min_section_chars: int = 300,
) -> list[DocChunk]:
    """Pack units into chunks that keep their page range and heading.

    Consecutive units with the same heading form a section. A section shorter
    than ``min_section_chars`` is merged into the following section (it keeps
    the following section's heading). Within a section the sentences are
    packed by ``chunk_segments``; the chunk's page range is the min and max
    page of the sentences it holds. When a heading exists the chunk text is
    prefixed with it once, so the embedded text carries its context the way
    card text carries its thread context.
    """
    sections = _sections(units, min_section_chars)
    chunks: list[DocChunk] = []
    for heading, section_units in sections:
        segments = [(u.text, (u.page, u.heading)) for u in section_units]
        for text, tags in chunk_segments(segments, target_chars, overlap_chars):
            if not text.strip():
                continue
            pages = [p for p, _ in tags if p is not None]
            body = f"{heading}\n{text}" if heading else text
            chunks.append(DocChunk(
                chunk_index=len(chunks),
                text=body,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                heading=heading,
                char_count=len(body),
            ))
    return chunks


def _sections(units: list[TextUnit], min_section_chars: int) -> list[tuple[str | None, list[TextUnit]]]:
    """Group consecutive same-heading units, merging tiny sections forward."""
    grouped: list[tuple[str | None, list[TextUnit]]] = []
    for u in units:
        if grouped and grouped[-1][0] == u.heading:
            grouped[-1][1].append(u)
        else:
            grouped.append((u.heading, [u]))

    merged: list[tuple[str | None, list[TextUnit]]] = []
    carry: list[TextUnit] = []
    for i, (heading, section_units) in enumerate(grouped):
        size = sum(len(u.text) for u in section_units)
        is_last = i == len(grouped) - 1
        if size < min_section_chars and not is_last:
            carry.extend(section_units)
            continue
        merged.append((heading, carry + section_units))
        carry = []
    if carry:
        # Only reachable when every section was tiny; keep the text under the
        # last heading rather than drop it.
        if merged:
            merged[-1][1].extend(carry)
        else:
            merged.append((grouped[-1][0] if grouped else None, carry))
    return merged
