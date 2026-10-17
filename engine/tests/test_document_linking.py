# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for document identifier extraction and entity linking."""

import pytest

from laya.documents.linking import (
    DOC_IDENTIFIER_RE,
    MAX_LINKS_PER_DOCUMENT,
    entity_refs_csv,
    extract_identifiers,
    resolve_entity_links,
)
from laya.documents.parsers.base import TextUnit
from tests.conftest import insert_test_card


class TestDocIdentifierRe:
    @pytest.mark.parametrize("text, expected", [
        ("see PROJ-123 for details", ["PROJ-123"]),
        ("merged PR #540 yesterday", ["PR-540"]),
        ("PR 541 and MR-7 and GH-12 and ISSUE #9", ["PR-541", "MR-7", "GH-12", "ISSUE-9"]),
        ("ABC#42 is open", ["ABC-42"]),
    ])
    def test_positives(self, text, expected):
        assert [i for i, _ in extract_identifiers([TextUnit(text=text)])] == expected

    @pytest.mark.parametrize("text", [
        "Figure 3 shows the plan",
        "Q4 2025 targets",
        "page 12 of the report",
        "version 2.0 shipped",
        "the x-1 variable",
    ])
    def test_negatives(self, text):
        assert DOC_IDENTIFIER_RE.search(text) is None

    def test_first_page_and_dedupe(self):
        units = [TextUnit(text="PAY-1 intro", page=1), TextUnit(text="PAY-1 again and PAY-2", page=3)]
        assert extract_identifiers(units) == [("PAY-1", 1), ("PAY-2", 3)]


@pytest.mark.asyncio
class TestResolveEntityLinks:
    async def test_identifier_links_to_card_entity(self, db):
        await insert_test_card(db, card_id="card_a", event_id="evt_a", header="Fix PAY-42 checkout",
                               entity_id="jira:ticket:PAY-42", space_id="default")
        units = [TextUnit(text="Ticket PAY-42 tracks the redesign.", page=2)]
        links = await resolve_entity_links("default", units, extract_identifiers(units))
        assert links == [{
            "entity_id": "jira:ticket:PAY-42", "link_method": "identifier", "confidence": 1.0,
            "mention_count": 1, "first_page": 2, "matched_text": "PAY-42",
        }]

    async def test_space_scopes_card_matches(self, db):
        await insert_test_card(db, card_id="card_a", event_id="evt_a", header="PAY-42",
                               entity_id="jira:ticket:PAY-42", space_id="other")
        units = [TextUnit(text="PAY-42")]
        assert await resolve_entity_links("default", units, extract_identifiers(units)) == []

    async def test_entity_ref_and_mention(self, db):
        await db.execute(
            "INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs) VALUES (?, ?, ?, ?)",
            ("jira:ticket:OPS-9", "ticket", "Checkout Redesign", '{"jira": ["OPS-9"]}'),
        )
        await db.commit()
        units = [TextUnit(text="The checkout redesign (OPS-9) is on track.", page=4)]
        links = await resolve_entity_links(None, units, extract_identifiers(units))
        assert len(links) == 1
        link = links[0]
        # Highest-confidence method wins; the mention still counts.
        assert link["link_method"] == "entity_ref"
        assert link["confidence"] == 0.9
        assert link["first_page"] == 4

    async def test_mention_only(self, db):
        await db.execute(
            "INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs) VALUES (?, ?, ?, ?)",
            ("slack:thread:x", "thread", "Fraud Model Retraining", "{}"),
        )
        await db.commit()
        units = [TextUnit(text="fraud model retraining is blocked; fraud model retraining needs data")]
        links = await resolve_entity_links(None, units, [])
        assert links[0]["link_method"] == "mention"
        assert links[0]["mention_count"] == 2
        assert links[0]["confidence"] == 0.6

    async def test_cap(self, db):
        for i in range(MAX_LINKS_PER_DOCUMENT + 20):
            await db.execute(
                "INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs) VALUES (?, ?, ?, ?)",
                (f"e:{i}", "ticket", f"Entity Name {i:04d}", "{}"),
            )
        await db.commit()
        text = " ".join(f"entity name {i:04d}" for i in range(MAX_LINKS_PER_DOCUMENT + 20))
        links = await resolve_entity_links(None, [TextUnit(text=text)], [])
        assert len(links) == MAX_LINKS_PER_DOCUMENT


def test_entity_refs_csv_capped():
    links = [{"entity_id": f"e{i}"} for i in range(30)]
    assert entity_refs_csv(links).count(",") == 19
