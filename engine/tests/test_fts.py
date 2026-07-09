# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for FTS5/BM25 keyword search (db/fts.py) and its integration into the
chat, card-search-tool, and trace retrieval paths."""

import pytest

from laya.db.fts import build_fts_match, fts_ready
from tests.conftest import insert_test_card


class TestBuildFtsMatch:
    def test_none_for_empty(self):
        assert build_fts_match("") is None
        assert build_fts_match("   ") is None

    def test_none_for_stopwords_only(self):
        assert build_fts_match("the and or to of") is None

    def test_short_tokens_filtered(self):
        assert build_fts_match("a bc", min_len=3) is None

    def test_or_of_quoted_phrases(self):
        assert build_fts_match("payment service crash") == '"payment" OR "service" OR "crash"'

    def test_operators_become_literal_phrases(self):
        # FTS5 operators / punctuation in user input must not break MATCH parsing.
        # "AND" lowercases to a stopword and drops out; the rest become phrases.
        assert build_fts_match("foo* AND (bar)", min_len=2) == '"foo*" OR "(bar)"'

    def test_embedded_quotes_doubled(self):
        assert build_fts_match('foo"bar', min_len=2) == '"foo""bar"'

    def test_max_terms_cap(self):
        assert build_fts_match("alpha beta gamma delta", max_terms=2) == '"alpha" OR "beta"'

    def test_match_all_ands_phrases(self):
        # match_all=True joins with AND so every term must be present — used by the
        # card-search tool so its FTS path matches its LIKE fallback (P7-1).
        assert (
            build_fts_match("payment service crash", match_all=True)
            == '"payment" AND "service" AND "crash"'
        )


@pytest.mark.asyncio
class TestFtsCardSearch:
    async def test_fts_ready_in_test_db(self, db):
        """The conftest db fixture builds FTS tables, so the BM25 path is active."""
        assert fts_ready() is True

    async def test_bm25_matches_relevant_card_not_irrelevant(self, db):
        await insert_test_card(
            db, card_id="card_pay", event_id="evt_pay",
            header="Payment service crash",
            summary="Null pointer in PaymentService on null customer id",
            entity_id="jira:ticket:PAY-1",
        )
        await insert_test_card(
            db, card_id="card_news", event_id="evt_news",
            header="Weekly marketing newsletter",
            summary="Company digest and product updates",
            entity_id="gmail:thread:n1",
        )
        from laya.pipeline.chat import _card_keyword_search

        ids = [r["card_id"] for r in await _card_keyword_search("payment crash", None, 10)]
        assert "card_pay" in ids
        assert "card_news" not in ids

    async def test_contextual_bm25_finds_followup_via_thread_context(self, db):
        """A terse follow-up whose OWN text lacks the query term is still found
        when the term lives in its thread_context (Contextual BM25)."""
        await insert_test_card(
            db, card_id="card_fu", event_id="evt_fu",
            header="Resolved as Fixed", summary="Approved.",
            entity_id="jira:ticket:PAY-2",
        )
        # Trigger keeps cards_fts in sync with this update.
        await db.execute(
            "UPDATE action_cards SET thread_context = ? WHERE card_id = ?",
            ("Payment service crash investigation", "card_fu"),
        )
        await db.commit()
        from laya.pipeline.chat import _card_keyword_search

        ids = [r["card_id"] for r in await _card_keyword_search("payment", None, 10)]
        assert "card_fu" in ids  # matched only via thread_context

    async def test_space_filter_applied(self, db):
        await insert_test_card(
            db, card_id="card_s1", event_id="evt_s1", header="Deploy alpha",
            summary="deploy to alpha space", entity_id="github:pr:1", space_id="space_a",
        )
        await insert_test_card(
            db, card_id="card_s2", event_id="evt_s2", header="Deploy beta",
            summary="deploy to beta space", entity_id="github:pr:2", space_id="space_b",
        )
        from laya.pipeline.chat import _card_keyword_search

        ids = [r["card_id"] for r in await _card_keyword_search("deploy", "space_a", 10)]
        assert ids == ["card_s1"]


@pytest.mark.asyncio
class TestFtsToolAndTrace:
    async def test_card_tools_search_keyword_uses_fts(self, db):
        await insert_test_card(
            db, card_id="card_x", event_id="evt_x",
            header="Deploy pipeline failure",
            summary="CI deploy step failed on staging",
            entity_id="github:pr:9",
        )
        from laya.llm.tools.card_tools import _search_keyword

        rows, total, has_more = await _search_keyword(
            "deploy pipeline", None, None, 10, 0, None,
        )
        assert total >= 1
        assert any(r["card_id"] == "card_x" for r in rows)

    async def test_trace_event_keyword_maps_to_cards(self, db):
        # insert_test_event (via insert_test_card) sets subject_title "NPE in PaymentService".
        await insert_test_card(
            db, card_id="card_t", event_id="evt_t", entity_id="jira:ticket:BUG-1234",
        )
        from laya.pipeline.trace import _event_keyword_search

        results = await _event_keyword_search("PaymentService", None, 10)
        assert any(r["card_id"] == "card_t" for r in results)
