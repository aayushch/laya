# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the batched daily-summary fold (pipeline/summarize.py).

The key optimization these lock in: a debounce flush of N cards costs
ceil(N / batch_max) LLM calls, not N — and no card is ever lost, including
when the batched call fails and the per-card fallback kicks in.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline import summarize


def _card(i: int) -> dict:
    return {
        "card_id": f"card_{i}",
        "card_header": f"Header {i}",
        "card_summary": f"Summary {i}",
        "card_priority": "MEDIUM",
        "card_category": "engineering",
        "card_persona": "engineer",
        "card_intelligence": None,
        "actor_name": "Alice",
        "source_platform": "github",
        "card_tags": None,
    }


def _ok_response() -> MagicMock:
    r = MagicMock()
    r.parsed = {
        "events_and_meetings": [],
        "action_items": [{"text": "x", "card_id": "card_0", "priority": "MEDIUM", "status": "pending"}],
        "key_updates": [],
    }
    r.truncated = False
    r.output_tokens = 10
    r.model = "test-model"
    return r


async def _persisted_card_ids(db, space_id="default") -> list[str]:
    rows = await db.execute_fetchall(
        "SELECT card_ids FROM daily_summaries WHERE space_id = ?", (space_id,)
    )
    return json.loads(rows[0]["card_ids"]) if rows else []


class TestSummaryCompaction:
    """Deterministic dedup + hard cap keep the daily summary a summary, not a pile
    — the safety net over an LLM fold that drifts on a local model."""

    def test_dedupe_removes_exact_and_normalized_duplicates(self):
        items = [
            {"text": "Review PR #795 (IT-267)"},
            {"text": "Review PR #795 (IT-267)"},        # exact dupe
            {"text": "  review   pr #795 (IT-267)  "},   # whitespace/case dupe
            {"text": "Review PR #794"},
            {"text": ""},                                 # empty dropped
        ]
        out = summarize._dedupe_items(items)
        assert [i["text"] for i in out] == ["Review PR #795 (IT-267)", "Review PR #794"]

    def test_compact_dedupes_below_threshold(self):
        # A small summary (the 7-item case) still gets its exact dupe removed.
        summary = {
            "events_and_meetings": [],
            "action_items": [
                {"text": "Review PR #795", "status": "pending", "priority": "HIGH"},
                {"text": "Review PR #795", "status": "pending", "priority": "HIGH"},
            ],
            "key_updates": [],
        }
        out = summarize._compact_summary(summary)
        assert len(out["action_items"]) == 1

    def test_compact_drops_resolved_low_value_items(self):
        summary = {
            "events_and_meetings": [],
            "action_items": [
                {"text": "a", "status": "pending", "priority": "LOW"},     # kept (pending)
                {"text": "b", "status": "done", "priority": "LOW"},        # dropped
                {"text": "c", "status": "dismissed", "priority": "MEDIUM"},# dropped
                {"text": "d", "status": "done", "priority": "CRITICAL"},   # kept (high)
            ],
            "key_updates": [],
        }
        out = summarize._compact_summary(summary)
        assert {i["text"] for i in out["action_items"]} == {"a", "d"}

    def test_hard_cap_bounds_a_flood_of_unresolved_items(self):
        # 100 pending cards — nothing for the resolved-pruning to remove, so only
        # the hard cap keeps this from becoming a pile.
        cap = summarize._MAX_ITEMS_PER_SECTION
        summary = {
            "events_and_meetings": [],
            "action_items": [
                {"text": f"item {i}", "status": "pending", "priority": "MEDIUM"}
                for i in range(100)
            ],
            "key_updates": [],
        }
        out = summarize._compact_summary(summary)
        assert len(out["action_items"]) == cap

    def test_cap_keeps_important_items_first(self):
        cap = summarize._MAX_ITEMS_PER_SECTION
        # cap MEDIUM fillers + a couple of CRITICAL/pending items buried at the end.
        items = [{"text": f"m{i}", "status": "pending", "priority": "MEDIUM"} for i in range(cap + 5)]
        items.append({"text": "crit", "status": "pending", "priority": "CRITICAL"})
        summary = {"events_and_meetings": [], "action_items": items, "key_updates": []}
        out = summarize._compact_summary(summary)
        texts = {i["text"] for i in out["action_items"]}
        assert "crit" in texts                      # the important one survived the cap
        assert len(out["action_items"]) == cap


@pytest.mark.asyncio
async def test_batch_fold_uses_ceil_calls_not_one_per_card(db):
    """15 cards with batch_max=10 → 2 LLM calls, all 15 cards persisted."""
    calls = []

    async def fake_llm_call(**kwargs):
        calls.append(kwargs)
        return _ok_response()

    with patch.object(summarize, "llm_call", new=fake_llm_call), \
         patch.object(summarize, "_get_batch_max_cards", return_value=10), \
         patch("laya.pipeline.summarize.manager.broadcast", new_callable=AsyncMock):
        await summarize._run_summary_update("default", [_card(i) for i in range(15)], [])

    assert len(calls) == 2, f"expected ceil(15/10)=2 batched calls, got {len(calls)}"
    assert sorted(await _persisted_card_ids(db)) == sorted(f"card_{i}" for i in range(15))


@pytest.mark.asyncio
async def test_intra_flush_and_persisted_duplicates_are_skipped(db):
    """A card already in the summary and a card duplicated within the flush are folded once."""
    await db.execute(
        "INSERT INTO daily_summaries (date, space_id, summary_json, card_ids, updated_at) "
        "VALUES (strftime('%Y-%m-%d','now'), 'default', ?, ?, 'now')",
        (json.dumps({"events_and_meetings": [], "action_items": [], "key_updates": []}),
         json.dumps(["card_0"])),
    )
    await db.commit()

    calls = []

    async def fake_llm_call(**kwargs):
        calls.append(kwargs)
        return _ok_response()

    # card_0 is already persisted; card_1 appears twice in the flush.
    flush = [_card(0), _card(1), _card(1), _card(2)]
    with patch.object(summarize, "llm_call", new=fake_llm_call), \
         patch.object(summarize, "_get_batch_max_cards", return_value=10), \
         patch("laya.pipeline.summarize.manager.broadcast", new_callable=AsyncMock):
        await summarize._run_summary_update("default", flush, [])

    assert len(calls) == 1
    assert sorted(await _persisted_card_ids(db)) == ["card_0", "card_1", "card_2"]


@pytest.mark.asyncio
async def test_batch_failure_falls_back_to_per_card(db):
    """When the batched fold fails, every card is still folded via the per-card fallback."""
    seen_batch = []

    async def fake_llm_call(**kwargs):
        # The batch prompt carries the "[NEW CARDS]" marker; single-card carries "[NEW CARD]".
        content = kwargs["messages"][-1]["content"]
        if "[NEW CARDS]" in content:
            seen_batch.append(content)
            raise RuntimeError("simulated batch truncation")
        return _ok_response()

    with patch.object(summarize, "llm_call", new=fake_llm_call), \
         patch.object(summarize, "_get_batch_max_cards", return_value=10), \
         patch("laya.pipeline.summarize.manager.broadcast", new_callable=AsyncMock):
        await summarize._run_summary_update("default", [_card(i) for i in range(3)], [])

    assert seen_batch, "batch path should have been attempted"
    assert sorted(await _persisted_card_ids(db)) == ["card_0", "card_1", "card_2"]
