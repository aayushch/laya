# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for context-rule consolidation (pipeline/context_learn.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline.context_learn import maybe_consolidate_context_rules

SPACE = "s1"


async def _insert_rule(db, rule_text, *, source="learned", space_id=SPACE):
    await db.execute(
        """INSERT INTO context_rules (space_id, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, ?, 1, datetime('now'), datetime('now'))""",
        (space_id, rule_text, source),
    )
    await db.commit()


async def _rules(db, source="learned", space_id=SPACE):
    rows = await db.execute_fetchall(
        "SELECT rule_text FROM context_rules WHERE source = ? AND space_id = ? ORDER BY id",
        (source, space_id),
    )
    return [r["rule_text"] for r in rows]


def _llm(rules):
    resp = MagicMock()
    resp.parsed = {"rules": [{"rule_text": t, "reasoning": "x"} for t in rules]}
    return resp


@pytest.mark.asyncio
class TestContextRuleConsolidation:
    async def test_below_threshold_is_noop(self, db):
        await _insert_rule(db, "only one")
        with patch("laya.pipeline.context_learn._consolidation_threshold", return_value=5), \
             patch("laya.pipeline.context_learn.llm_call", new_callable=AsyncMock) as mock_llm:
            result = await maybe_consolidate_context_rules(SPACE)
        assert result == 0
        mock_llm.assert_not_called()
        assert await _rules(db) == ["only one"]

    async def test_consolidates_and_preserves_manual(self, db):
        for i in range(4):
            await _insert_rule(db, f"learned {i}")
        await _insert_rule(db, "my manual rule", source="manual")

        with patch("laya.pipeline.context_learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.context_learn.llm_call", new_callable=AsyncMock,
                   return_value=_llm(["merged A", "merged B"])):
            result = await maybe_consolidate_context_rules(SPACE)

        assert result == 2
        assert sorted(await _rules(db, "learned")) == ["merged A", "merged B"]
        # manual rule untouched
        assert await _rules(db, "manual") == ["my manual rule"]

    async def test_guardrail_skips_when_not_smaller(self, db):
        for i in range(3):
            await _insert_rule(db, f"learned {i}")
        # LLM returns >= input count → no benefit → must not apply
        with patch("laya.pipeline.context_learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.context_learn.llm_call", new_callable=AsyncMock,
                   return_value=_llm(["a", "b", "c"])):
            result = await maybe_consolidate_context_rules(SPACE)
        assert result == 0
        assert sorted(await _rules(db)) == ["learned 0", "learned 1", "learned 2"]

    async def test_guardrail_skips_when_empty(self, db):
        for i in range(3):
            await _insert_rule(db, f"learned {i}")
        with patch("laya.pipeline.context_learn._consolidation_threshold", return_value=2), \
             patch("laya.pipeline.context_learn.llm_call", new_callable=AsyncMock,
                   return_value=_llm([])):
            result = await maybe_consolidate_context_rules(SPACE)
        assert result == 0
        assert len(await _rules(db)) == 3
