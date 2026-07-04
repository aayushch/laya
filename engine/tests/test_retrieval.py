# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the consolidated retrieval primitives (laya/retrieval.py — P7-1)."""

from laya.retrieval import STOPWORDS, extract_keywords, reciprocal_rank_fusion


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
