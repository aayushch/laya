# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Sentence-boundary text chunking for embedding."""

from __future__ import annotations

import re
from typing import TypeVar

# Target ~350 tokens per chunk, ~50 token overlap
# Rough approximation: 1 token ≈ 4 characters
_CHUNK_TARGET_CHARS = 1400   # ~350 tokens
_CHUNK_OVERLAP_CHARS = 200   # ~50 tokens

# Split on sentence boundaries: period/question/exclamation followed by space and capital,
# or double newlines (paragraph breaks)
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z"])|(?:\n\s*\n)')


T = TypeVar("T")


def chunk_segments(
    segments: list[tuple[str, T]],
    target_chars: int = _CHUNK_TARGET_CHARS,
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,
) -> list[tuple[str, list[T]]]:
    """Split tagged text segments into overlapping chunks at sentence boundaries.

    Each segment is ``(text, tag)``; the tag names where the text came from
    (a page number, a heading) and travels with every sentence of that
    segment. The sentence packing and overlap are the same as ``chunk_text``.
    Each returned chunk is ``(chunk_text, tags)`` where ``tags`` lists the
    distinct tags of the sentences in the chunk, in order of first appearance.

    A single untagged segment reproduces ``chunk_text`` exactly, including the
    one-chunk short-text path and the guarantee of no empty chunks.
    """
    # Short input: one chunk. Mirrors chunk_text so callers see identical
    # output for a single segment.
    joined = "\n\n".join(seg.strip() for seg, _ in segments if seg.strip())
    if not joined:
        return [("", [tag for _, tag in segments[:1]])]
    # Allow 20% overflow to avoid tiny trailing chunks
    if len(joined) <= int(target_chars * 1.2):
        tags = _distinct([tag for seg, tag in segments if seg.strip()])
        return [(joined, tags)]

    sentences: list[tuple[str, T]] = []
    for seg, tag in segments:
        for part in _SENTENCE_BOUNDARY.split(seg.strip()):
            part = part.strip()
            if part:
                sentences.append((part, tag))

    if not sentences:
        return [(joined, _distinct([tag for _, tag in segments]))]

    chunks: list[tuple[str, list[T]]] = []
    current_parts: list[tuple[str, T]] = []
    current_len = 0

    for sentence, tag in sentences:
        slen = len(sentence)

        # If adding this sentence exceeds target and we have content, flush
        if current_len + slen > target_chars and current_parts:
            chunks.append(_flush(current_parts))

            # Build overlap: keep last sentences that fit in overlap window
            overlap_parts: list[tuple[str, T]] = []
            overlap_len = 0
            for s, t in reversed(current_parts):
                if overlap_len + len(s) > overlap_chars:
                    break
                overlap_parts.insert(0, (s, t))
                overlap_len += len(s)

            current_parts = overlap_parts
            current_len = overlap_len

        current_parts.append((sentence, tag))
        current_len += slen

    # Flush remaining
    if current_parts:
        final = _flush(current_parts)
        # Avoid duplicate if last chunk is same as current
        if not chunks or final[0] != chunks[-1][0]:
            chunks.append(final)

    return chunks if chunks else [(joined, _distinct([tag for _, tag in segments]))]


def _flush(parts: list[tuple[str, T]]) -> tuple[str, list[T]]:
    return " ".join(s for s, _ in parts), _distinct([t for _, t in parts])


def _distinct(tags: list[T]) -> list[T]:
    """Tags in order of first appearance, duplicates dropped. Tags may be
    unhashable (tuples with None are fine; dicts are not expected)."""
    out: list[T] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out


def chunk_text(
    text: str,
    target_chars: int = _CHUNK_TARGET_CHARS,
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries.

    Returns single-element list if text fits in one chunk.
    Guarantees no empty chunks are returned.
    """
    return [c for c, _ in chunk_segments([(text, None)], target_chars, overlap_chars)]
