# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for Omni change summaries — item keys and version diffing."""

import pytest

from laya.pipeline.omni_change import (
    compute_incremental_change_summary,
    compute_item_key,
    compute_resynthesis_change_summary,
    decorate_item_keys,
    fold_counts_by_section,
    merge_change_summaries,
    parse_change_summary,
    resolved_counts_by_section,
    subject_matches,
)

TERMINAL = {"done", "dismissed", "archived"}


def _item(text, *, entities=None, cards=None, platforms=None):
    return {
        "text": text,
        "entity_ids": list(entities or []),
        "source_cards": list(cards or []),
        "platforms": list(platforms or []),
    }


def _sections(**by_type):
    return [{"type": t, "items": items} for t, items in by_type.items()]


class TestItemKey:
    def test_stable_regardless_of_entity_order(self):
        a = compute_item_key("recent", ["github:pr:1451", "gmail:msg:9"])
        b = compute_item_key("recent", ["gmail:msg:9", "github:pr:1451"])
        assert a == b

    def test_duplicates_do_not_change_the_key(self):
        a = compute_item_key("recent", ["github:pr:1"])
        b = compute_item_key("recent", ["github:pr:1", "github:pr:1"])
        assert a == b

    def test_section_is_part_of_the_identity(self):
        assert compute_item_key("recent", ["x:1"]) != compute_item_key("period", ["x:1"])

    def test_falls_back_to_source_cards_without_entities(self):
        key = compute_item_key("recent", [], ["card_b", "card_a"])
        assert key == compute_item_key("recent", None, ["card_a", "card_b"])
        # …and is distinct from the entity-keyed form for the same section
        assert key != compute_item_key("recent", ["card_a"])

    def test_key_is_twelve_hex_chars(self):
        key = compute_item_key("attention", ["gmail:thread:7"])
        assert len(key) == 12
        assert all(c in "0123456789abcdef" for c in key)

    def test_decorate_stamps_every_item(self):
        sections = _sections(
            attention=[_item("a", entities=["x:1"])],
            recent=[_item("b", entities=["y:2"]), _item("c", cards=["card_1"])],
        )
        decorate_item_keys(sections)
        keys = [i["item_key"] for s in sections for i in s["items"]]
        assert len(keys) == 3
        assert len(set(keys)) == 3

    def test_decorate_is_idempotent(self):
        sections = _sections(recent=[_item("b", entities=["y:2"])])
        first = decorate_item_keys(sections)[0]["items"][0]["item_key"]
        second = decorate_item_keys(sections)[0]["items"][0]["item_key"]
        assert first == second

    def test_content_free_items_do_not_collide(self):
        """Two items with no entity_ids AND no source_cards used to hash
        identically — a duplicate key, which is a hard render error in the UI and
        took the whole board down. Text separates them."""
        a = compute_item_key("milestone", [], [], "Job search & coordination (2)")
        b = compute_item_key("milestone", [], [], "Server access & permissions (5)")
        assert a != b

    def test_decorate_forces_uniqueness_within_a_section(self):
        """Even identical text can't produce a repeat — the suffix guarantees it."""
        sections = _sections(milestone=[_item("same"), _item("same"), _item("same")])
        decorate_item_keys(sections)
        keys = [i["item_key"] for i in sections[0]["items"]]
        assert len(set(keys)) == 3
        assert keys[0] == compute_item_key("milestone", [], [], "same")
        assert keys[1].endswith("-1") and keys[2].endswith("-2")

    def test_uniqueness_suffix_is_deterministic(self):
        build = lambda: _sections(milestone=[_item("same"), _item("same")])  # noqa: E731
        first = [i["item_key"] for i in decorate_item_keys(build())[0]["items"]]
        second = [i["item_key"] for i in decorate_item_keys(build())[0]["items"]]
        assert first == second

    def test_same_key_in_different_sections_is_allowed(self):
        """Sections are keyed independently in the UI, and section is part of the
        hash anyway — no suffix should be applied across them."""
        sections = _sections(recent=[_item("x", entities=["a:1"])],
                             period=[_item("x", entities=["a:1"])])
        decorate_item_keys(sections)
        assert not sections[0]["items"][0]["item_key"].endswith("-1")
        assert not sections[1]["items"][0]["item_key"].endswith("-1")

    def test_changelog_uses_the_stamped_key(self):
        """A suffixed item must be named in the changelog by its suffixed key, or
        the drill-down link 404s."""
        sections = _sections(recent=[_item("dup"), _item("dup")])
        decorate_item_keys(sections)
        summary = compute_incremental_change_summary(sections[0]["items"], "recent")
        assert [e["item_key"] for e in summary["added"]] == [
            i["item_key"] for i in sections[0]["items"]
        ]


class TestSubjectMatching:
    def test_matches_on_entity_overlap(self):
        assert subject_matches(
            _item("old", entities=["github:pr:1", "github:pr:2"]),
            _item("new", entities=["github:pr:2"]),
        )

    def test_matches_on_card_overlap_when_entities_missing(self):
        assert subject_matches(_item("old", cards=["c1"]), _item("new", cards=["c1", "c2"]))

    def test_no_overlap_is_no_match(self):
        assert not subject_matches(_item("a", entities=["x:1"]), _item("b", entities=["y:2"]))


class TestIncrementalSummary:
    def test_added_items_become_added_entries(self):
        summary = compute_incremental_change_summary(
            [_item("PR #1451 needs review", entities=["github:pr:1451"],
                   cards=["c1", "c2"], platforms=["github"])]
        )
        assert summary["counts"] == {"added": 1, "folded": 0, "resolved": 0}
        entry = summary["added"][0]
        assert entry["section"] == "recent"
        assert entry["source_count"] == 2
        assert entry["platforms"] == ["github"]
        assert entry["item_key"] == compute_item_key("recent", ["github:pr:1451"])

    def test_no_additions_yields_zero_counts(self):
        assert compute_incremental_change_summary([])["counts"]["added"] == 0


class TestResynthesisSummary:
    def test_new_item_is_added(self):
        summary = compute_resynthesis_change_summary(
            _sections(recent=[]),
            _sections(recent=[_item("fresh", entities=["x:1"])]),
            {},
            TERMINAL,
        )
        assert summary["counts"]["added"] == 1
        assert summary["added"][0]["text"] == "fresh"

    def test_item_moving_down_the_chain_is_folded(self):
        prior = _sections(recent=[_item("3 PRs opened", entities=["github:pr:1"])])
        new = _sections(period=[_item("23 PRs merged this week", entities=["github:pr:1"])])
        summary = compute_resynthesis_change_summary(prior, new, {}, TERMINAL)

        assert summary["counts"] == {"added": 0, "folded": 1, "resolved": 0}
        fold = summary["folded"][0]
        assert fold["from_section"] == "recent"
        assert fold["to_section"] == "period"
        assert fold["from_text"] == "3 PRs opened"
        assert fold["to_text"] == "23 PRs merged this week"

    def test_vanished_item_with_all_cards_terminal_is_resolved(self):
        prior = _sections(
            attention=[_item("PR #1443 awaiting review", entities=["github:pr:1443"], cards=["c1"])]
        )
        meta = {"c1": {"status": "done", "resolved_at": "2026-05-04 14:22:00"}}
        summary = compute_resynthesis_change_summary(prior, _sections(recent=[]), meta, TERMINAL)

        assert summary["counts"] == {"added": 0, "folded": 0, "resolved": 1}
        resolved = summary["resolved"][0]
        assert resolved["section"] == "attention"
        assert resolved["entity_ids"] == ["github:pr:1443"]
        assert resolved["resolved_at"] == "2026-05-04 14:22:00"

    def test_resolved_at_prefers_the_entity_level_timestamp(self):
        """The card that resolves a subject is usually a NEW card on the same
        entity, so the entity-keyed map wins over the aggregate's own cards."""
        prior = _sections(attention=[_item("x", entities=["github:pr:1"], cards=["c1"])])
        meta = {"c1": {"status": "archived", "resolved_at": "2026-05-04 09:00:00"}}
        summary = compute_resynthesis_change_summary(
            prior, _sections(recent=[]), meta, TERMINAL,
            {"github:pr:1": "2026-05-04 14:22:00"},
        )
        assert summary["resolved"][0]["resolved_at"] == "2026-05-04 14:22:00"

    def test_vanished_item_still_open_is_folded_with_no_destination(self):
        prior = _sections(recent=[_item("still open", entities=["x:1"], cards=["c1"])])
        meta = {"c1": {"status": "ready"}}
        summary = compute_resynthesis_change_summary(prior, _sections(recent=[]), meta, TERMINAL)

        assert summary["counts"]["resolved"] == 0
        assert summary["folded"][0]["to_section"] is None

    def test_unchanged_item_produces_nothing(self):
        sections = _sections(recent=[_item("same", entities=["x:1"])])
        summary = compute_resynthesis_change_summary(sections, sections, {}, TERMINAL)
        assert summary["counts"] == {"added": 0, "folded": 0, "resolved": 0}

    def test_one_prior_item_matches_only_one_new_item(self):
        """Two prior lines folding into one aggregate must not both claim it —
        the second falls through to the not-in-new branch."""
        prior = _sections(recent=[
            _item("a", entities=["x:1"], cards=["c1"]),
            _item("b", entities=["x:2"], cards=["c2"]),
        ])
        new = _sections(period=[_item("merged aggregate", entities=["x:1", "x:2"])])
        meta = {"c1": {"status": "ready"}, "c2": {"status": "ready"}}
        summary = compute_resynthesis_change_summary(prior, new, meta, TERMINAL)

        assert summary["counts"]["added"] == 0
        assert summary["counts"]["folded"] == 2
        destinations = sorted(str(f["to_section"]) for f in summary["folded"])
        assert destinations == ["None", "period"]

    def test_missing_cards_do_not_count_as_resolved(self):
        """No card rows at all means unknown, not finished — otherwise a purged
        aggregate would be announced as work the user completed."""
        prior = _sections(attention=[_item("x", entities=["x:1"], cards=["gone"])])
        summary = compute_resynthesis_change_summary(prior, _sections(recent=[]), {}, TERMINAL)
        assert summary["counts"]["resolved"] == 0
        assert summary["counts"]["folded"] == 1


class TestMerge:
    def test_added_then_resolved_reports_once_as_resolved(self):
        key = compute_item_key("attention", ["x:1"])
        v1 = {"added": [{"item_key": key, "section": "attention", "text": "t"}],
              "folded": [], "resolved": []}
        v2 = {"added": [], "folded": [],
              "resolved": [{"item_key": key, "section": "attention", "text": "t"}]}
        merged = merge_change_summaries([v1, v2])
        assert merged["counts"] == {"added": 0, "folded": 0, "resolved": 1}

    def test_two_folds_chain_into_one(self):
        key = "abc123"
        v1 = {"added": [], "resolved": [], "folded": [
            {"item_key": key, "from_section": "recent", "to_section": "period",
             "from_text": "R", "to_text": "P"}]}
        v2 = {"added": [], "resolved": [], "folded": [
            {"item_key": key, "from_section": "period", "to_section": "milestone",
             "from_text": "P", "to_text": "M"}]}
        merged = merge_change_summaries([v1, v2])
        assert merged["counts"]["folded"] == 1
        fold = merged["folded"][0]
        assert fold["from_section"] == "recent"
        assert fold["to_section"] == "milestone"
        assert fold["from_text"] == "R"
        assert fold["to_text"] == "M"

    def test_distinct_items_all_survive(self):
        v1 = {"added": [{"item_key": "a", "text": "1"}], "folded": [], "resolved": []}
        v2 = {"added": [{"item_key": "b", "text": "2"}], "folded": [], "resolved": []}
        assert merge_change_summaries([v1, v2])["counts"]["added"] == 2

    def test_empty_input_is_an_empty_summary(self):
        assert merge_change_summaries([])["counts"] == {"added": 0, "folded": 0, "resolved": 0}

    def test_nulls_in_the_range_are_skipped(self):
        assert merge_change_summaries([None, {"added": [{"item_key": "a"}]}])["counts"]["added"] == 1


class TestParsingAndCounts:
    def test_null_column_parses_to_none(self):
        assert parse_change_summary(None) is None

    def test_malformed_json_parses_to_none(self):
        assert parse_change_summary("{not json") is None

    def test_missing_kinds_are_filled_in(self):
        parsed = parse_change_summary('{"added": [{"item_key": "a"}]}')
        assert parsed["folded"] == [] and parsed["resolved"] == []
        assert parsed["counts"]["added"] == 1

    def test_fold_counts_group_by_source_section(self):
        summary = {"folded": [
            {"from_section": "recent"}, {"from_section": "recent"}, {"from_section": "period"},
        ]}
        assert fold_counts_by_section(summary) == {"recent": 2, "period": 1}

    def test_resolved_counts_group_by_section(self):
        summary = {"resolved": [{"section": "attention"}, {"section": "attention"}]}
        assert resolved_counts_by_section(summary) == {"attention": 2}

    def test_counts_helpers_tolerate_none(self):
        assert fold_counts_by_section(None) == {}
        assert resolved_counts_by_section(None) == {}
