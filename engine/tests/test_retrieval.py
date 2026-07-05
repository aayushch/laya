# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the consolidated retrieval primitives (laya/retrieval.py — P7-1)."""

from unittest.mock import patch

import pytest

from laya.retrieval import (
    STOPWORDS,
    extract_keywords,
    fts_or_like,
    reciprocal_rank_fusion,
)


class TestExtractKeywords:
    def test_drops_stopwords_and_short_terms(self):
        assert extract_keywords("the payment service crashed", min_len=3) == [
            "payment", "service", "crashed",
        ]

    def test_min_len_two_keeps_short(self):
        # trace uses min_len=2 (broader recall) vs chat's 3 (precise). "is" is a
        # stopword and drops out in both.
        assert extract_keywords("PR is v2", min_len=2) == ["PR", "v2"]
        assert extract_keywords("PR is v2", min_len=3) == []

    def test_preserves_original_case(self):
        assert extract_keywords("Payment BUG-1234", min_len=2) == ["Payment", "BUG-1234"]

    def test_max_terms_cap(self):
        assert extract_keywords("alpha beta gamma delta", min_len=2, max_terms=2) == [
            "alpha", "beta",
        ]

    def test_stopwords_nonempty(self):
        assert "the" in STOPWORDS and "and" in STOPWORDS


class TestReciprocalRankFusion:
    def test_fuses_and_ranks(self):
        # A doc appearing near the top of two lists outranks a doc in one list.
        a = [{"card_id": "x"}, {"card_id": "y"}]
        b = [{"card_id": "x"}, {"card_id": "z"}]
        fused = reciprocal_rank_fusion([a, b])
        assert fused[0]["card_id"] == "x"  # appears rank-0 in both
        ids = [d["card_id"] for d in fused]
        assert set(ids) == {"x", "y", "z"}

    def test_resolves_event_and_entity_ids(self):
        # The two former copies checked event_id OR entity_id but not both; the
        # unified version dedupes across id / card_id / event_id / entity_id.
        lists = [
            [{"event_id": "e1"}, {"entity_id": "ent1"}],
            [{"event_id": "e1"}],  # duplicate of the first list's e1
        ]
        fused = reciprocal_rank_fusion(lists)
        event_hits = [d for d in fused if d.get("event_id") == "e1"]
        entity_hits = [d for d in fused if d.get("entity_id") == "ent1"]
        assert len(event_hits) == 1  # e1 deduped across the two lists
        assert len(entity_hits) == 1
        # e1 (in both lists) outranks ent1 (in one).
        assert fused[0].get("event_id") == "e1"


class TestFtsOrLike:
    """The shared FTS-else-LIKE dispatch (P7-1 part 2). Chat, trace, and the
    card-search tool route their keyword search through this so the "try FTS,
    fall back to LIKE" contract can't drift between them."""

    async def _run(self, query, *, fts_available, fts_side_effect=None):
        calls = {"fts": 0, "like": 0}

        async def fts(match):
            calls["fts"] += 1
            if fts_side_effect:
                raise fts_side_effect
            return f"fts:{match}"

        async def like(q):
            calls["like"] += 1
            return f"like:{q}"

        with patch("laya.retrieval.fts_ready", return_value=fts_available):
            result = await fts_or_like(
                query, min_len=3, max_terms=8,
                fts=fts, like=like, warn_event="test_fallback",
            )
        return result, calls

    @pytest.mark.asyncio
    async def test_uses_fts_when_ready_with_terms(self):
        result, calls = await self._run("payment crash", fts_available=True)
        assert result.startswith("fts:")
        assert calls == {"fts": 1, "like": 0}

    @pytest.mark.asyncio
    async def test_falls_back_to_like_when_fts_unavailable(self):
        result, calls = await self._run("payment crash", fts_available=False)
        assert result == "like:payment crash"
        assert calls == {"fts": 0, "like": 1}

    @pytest.mark.asyncio
    async def test_falls_back_to_like_when_no_usable_terms(self):
        # Stopwords-only query yields no MATCH expression → LIKE path, no FTS call.
        result, calls = await self._run("the and or", fts_available=True)
        assert result == "like:the and or"
        assert calls == {"fts": 0, "like": 1}

    @pytest.mark.asyncio
    async def test_fts_runtime_error_degrades_to_like(self):
        # The safety net: a runtime FTS fault must not 500 — it logs and runs LIKE.
        result, calls = await self._run(
            "payment crash", fts_available=True,
            fts_side_effect=RuntimeError("fts5 blew up"),
        )
        assert result == "like:payment crash"
        assert calls == {"fts": 1, "like": 1}

    @pytest.mark.asyncio
    async def test_empty_query_skips_fts(self):
        result, calls = await self._run("", fts_available=True)
        assert result == "like:"
        assert calls == {"fts": 0, "like": 1}
