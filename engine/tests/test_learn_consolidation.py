# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for learned-classification-rule consolidation (pipeline/learn.py, P7-8/P4-8).

The classification-side twin of context-rule consolidation: caps unbounded growth
of learned priority/persona rules that would otherwise bloat the router prompt.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline.learn import (
    maybe_consolidate_classification_rules,
    run_learn_extraction,
)
from tests.conftest import insert_test_card

SPACE = "s1"


async def _insert_rule(db, field, rule_text, *, source="learned", space_id=SPACE):
    await db.execute(
        """INSERT INTO classification_rules
           (space_id, field, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, datetime('now'), datetime('now'))""",
        (space_id, field, rule_text, source),
    )
    await db.commit()


async def _rules(db, source="learned", space_id=SPACE):
    rows = await db.execute_fetchall(
        "SELECT field, rule_text FROM classification_rules "
        "WHERE source = ? AND space_id = ? ORDER BY id",
        (source, space_id),
    )
    return [(r["field"], r["rule_text"]) for r in rows]


def _llm(rules):
    """rules: list of (field, rule_text)."""
    resp = MagicMock()
    resp.parsed = {"rules": [{"field": f, "rule_text": t, "reasoning": "x"} for f, t in rules]}
    return resp


@pytest.mark.asyncio
class TestClassificationRuleConsolidation:
    async def test_below_threshold_is_noop(self, db):
        await _insert_rule(db, "priority", "only one")
        with patch("laya.pipeline.learn._consolidation_threshold", return_value=5), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock) as mock_llm:
            result = await maybe_consolidate_classification_rules(SPACE)
        assert result == 0
        mock_llm.assert_not_called()
        assert await _rules(db) == [("priority", "only one")]

    async def test_consolidates_and_preserves_field_and_manual(self, db):
        for i in range(3):
            await _insert_rule(db, "priority", f"prio {i}")
        await _insert_rule(db, "persona", "external -> COMMS")
        await _insert_rule(db, "priority", "my manual", source="manual")

        merged = [("priority", "PR-ish things are LOW"), ("persona", "external -> COMMS")]
        with patch("laya.pipeline.learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock, return_value=_llm(merged)):
            result = await maybe_consolidate_classification_rules(SPACE)

        assert result == 2
        # learned replaced with the consolidated set, fields preserved
        assert sorted(await _rules(db, "learned")) == sorted(merged)
        # manual rule untouched
        assert await _rules(db, "manual") == [("priority", "my manual")]

    async def test_guardrail_skips_when_not_smaller(self, db):
        for i in range(3):
            await _insert_rule(db, "priority", f"r{i}")
        same = [("priority", f"r{i}") for i in range(3)]
        with patch("laya.pipeline.learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock, return_value=_llm(same)):
            result = await maybe_consolidate_classification_rules(SPACE)
        assert result == 0
        assert len(await _rules(db, "learned")) == 3  # originals intact

    async def test_guardrail_skips_when_empty(self, db):
        for i in range(3):
            await _insert_rule(db, "persona", f"r{i}")
        with patch("laya.pipeline.learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock, return_value=_llm([])):
            result = await maybe_consolidate_classification_rules(SPACE)
        assert result == 0
        assert len(await _rules(db, "learned")) == 3

    async def test_only_touches_the_scoped_space(self, db):
        for i in range(3):
            await _insert_rule(db, "priority", f"s1 {i}", space_id="s1")
        await _insert_rule(db, "priority", "other space rule", space_id="s2")

        with patch("laya.pipeline.learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock,
                   return_value=_llm([("priority", "merged")])):
            result = await maybe_consolidate_classification_rules("s1")

        assert result == 1
        assert await _rules(db, "learned", space_id="s1") == [("priority", "merged")]
        assert await _rules(db, "learned", space_id="s2") == [("priority", "other space rule")]

    async def test_drops_rows_with_bad_field(self, db):
        for i in range(3):
            await _insert_rule(db, "priority", f"r{i}")
        # Model returns one valid + one junk-field item; junk is filtered out.
        resp = MagicMock()
        resp.parsed = {"rules": [
            {"field": "priority", "rule_text": "good", "reasoning": "x"},
            {"field": "nonsense", "rule_text": "bad", "reasoning": "x"},
        ]}
        with patch("laya.pipeline.learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock, return_value=resp):
            result = await maybe_consolidate_classification_rules(SPACE)
        assert result == 1
        assert await _rules(db, "learned") == [("priority", "good")]


@pytest.mark.asyncio
class TestRunLearnExtraction:
    """End-to-end over the refactored driver (shared fetch + mark-processed)."""

    async def _insert_correction(self, db, cid, space_id=SPACE):
        # card_id has an FK to action_cards — create the card first.
        await insert_test_card(db, card_id=f"card_{cid}", event_id=f"evt_{cid}", space_id=space_id)
        await db.execute(
            """INSERT INTO classification_corrections
               (id, card_id, space_id, field, original_value, corrected_value, card_summary,
                category, platform, event_type, processed, created_at)
               VALUES (?, ?, ?, 'priority', 'LOW', 'HIGH', 'summ', 'CODE', 'jira',
                       'issue_assigned', 0, datetime('now'))""",
            (cid, f"card_{cid}", space_id),
        )
        await db.commit()

    async def test_extracts_rules_and_marks_processed(self, db):
        for i in range(3):
            await self._insert_correction(db, i)

        learner = MagicMock()
        learner.parsed = {"rules": [
            {"field": "priority", "rule_text": "jira issue_assigned is HIGH", "reasoning": "x"},
        ]}
        learner.content = ""
        # Consolidation runs after but stays below the default threshold (40).
        with patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock, return_value=learner):
            created = await run_learn_extraction(SPACE)

        assert created == 1
        assert await _rules(db, "learned") == [("priority", "jira issue_assigned is HIGH")]
        # All fetched corrections consumed.
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM classification_corrections WHERE processed = 0", ()
        )
        assert rows[0]["n"] == 0

    async def test_no_corrections_is_noop(self, db):
        with patch("laya.pipeline.learn.llm_call", new_callable=AsyncMock) as mock_llm:
            created = await run_learn_extraction(SPACE)
        assert created == 0
        mock_llm.assert_not_called()
