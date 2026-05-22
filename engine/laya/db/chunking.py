# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Sentence-boundary text chunking for embedding."""

from __future__ import annotations

import re

# Target ~350 tokens per chunk, ~50 token overlap
# Rough approximation: 1 token ≈ 4 characters
_CHUNK_TARGET_CHARS = 1400   # ~350 tokens
_CHUNK_OVERLAP_CHARS = 200   # ~50 tokens

# Split on sentence boundaries: period/question/exclamation followed by space and capital,
# or double newlines (paragraph breaks)
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z"])|(?:\n\s*\n)')


def chunk_text(
    text: str,
    target_chars: int = _CHUNK_TARGET_CHARS,
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries.

    Returns single-element list if text fits in one chunk.
    Guarantees no empty chunks are returned.
    """
    text = text.strip()
    if not text:
        return [text] if text else [""]

    # Allow 20% overflow to avoid tiny trailing chunks
    if len(text) <= int(target_chars * 1.2):
        return [text]

    sentences = _SENTENCE_BOUNDARY.split(text)
    # Filter empty splits
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [text]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for sentence in sentences:
        slen = len(sentence)

        # If adding this sentence exceeds target and we have content, flush
        if current_len + slen > target_chars and current_parts:
            chunks.append(" ".join(current_parts))

            # Build overlap: keep last sentences that fit in overlap window
            overlap_parts: list[str] = []
            overlap_len = 0
            for s in reversed(current_parts):
                if overlap_len + len(s) > overlap_chars:
                    break
                overlap_parts.insert(0, s)
                overlap_len += len(s)

            current_parts = overlap_parts
            current_len = overlap_len

        current_parts.append(sentence)
        current_len += slen

    # Flush remaining
    if current_parts:
        final = " ".join(current_parts)
        # Avoid duplicate if last chunk is same as current
        if not chunks or final != chunks[-1]:
            chunks.append(final)

    return chunks if chunks else [text]
