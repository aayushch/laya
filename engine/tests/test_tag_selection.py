# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for select_prompt_tags: the short list of existing tag names that the
stager is shown for one event."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from laya.db.timeutil import db_ts
from laya.llm.prompts.stager import build_stager_messages
from laya.pipeline import tags as tags_module
from laya.pipeline.tags import (
    PROMPT_TAGS_SHOW_ALL_MAX,
    PROMPT_TAGS_TEXT_MATCH_MAX,
    select_prompt_tags,
)
from tests.conftest import insert_test_card, insert_test_event


async def _tag(db, name: str, system: int = 0) -> int:
    await db.execute("INSERT INTO tags (name, is_system) VALUES (?, ?)", (name, system))
    rows = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
    return rows[0]["tag_id"]


async def _assign(db, name: str, target_id: str, *, target_type: str = "card", days_ago: int = 0):
    rows = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (name,))
    tag_id = rows[0]["tag_id"] if rows else await _tag(db, name)
    created_at = db_ts(datetime.now(timezone.utc) - timedelta(days=days_ago))
    await db.execute(
        """INSERT INTO tag_assignments (tag_id, target_type, target_id, assigned_by, created_at)
           VALUES (?, ?, ?, 'stager', ?)""",
        (tag_id, target_type, target_id, created_at),
    )


async def _fill(db, count: int = PROMPT_TAGS_SHOW_ALL_MAX + 1):
    """Insert enough unused tags that the table is over the show-all limit. The
    names share no word with sample_event's text."""
    for i in range(count):
        await _tag(db, f"zzfiller-{i:04d}")
    await db.commit()


@pytest.mark.asyncio
class TestSelectPromptTags:
    async def test_small_table_shows_every_tag(self, db, sample_event):
        # The tags migration seeds three system tags.
        seeded = [r["name"] for r in await db.execute_fetchall("SELECT name FROM tags ORDER BY name")]
        await _tag(db, "billing")
        await _tag(db, "alpha")
        await db.commit()

        assert await select_prompt_tags(db, sample_event) == sorted([*seeded, "alpha", "billing"])

    async def test_large_table_without_any_signal_shows_only_system_tags(self, db, sample_event):
        await _fill(db)
        system = {r["name"] for r in await db.execute_fetchall("SELECT name FROM tags WHERE is_system = 1")}

        assert set(await select_prompt_tags(db, sample_event)) == system

    async def test_tags_of_the_same_entity_come_first(self, db, sample_event):
        await _fill(db)
        await insert_test_card(db, "card_old", "evt_old", entity_id=sample_event.entity_id)
        await insert_test_card(db, "card_other", "evt_other", entity_id="jira:ticket:OTHER-1")
        await _assign(db, "payments", "card_old", days_ago=90)
        await _assign(db, "on-entity", sample_event.entity_id, target_type="entity", days_ago=90)
        # A tag on another entity's old card, from a different actor, is not shown.
        await db.execute("UPDATE events SET actor_email = 'bob@company.com' WHERE event_id = 'evt_other'")
        await _assign(db, "unrelated", "card_other", days_ago=90)
        await db.commit()

        selected = await select_prompt_tags(db, sample_event)

        assert selected[:2] == ["on-entity", "payments"]
        assert "unrelated" not in selected

    async def test_own_card_is_not_a_source(self, db, sample_event):
        """On a reprocess the event's card exists already. Its old tags are not
        fed back to the stager."""
        await _fill(db)
        await insert_test_card(db, "card_own", sample_event.event_id, entity_id=sample_event.entity_id)
        await _assign(db, "stale-tag", "card_own", days_ago=90)
        await db.commit()

        assert "stale-tag" not in await select_prompt_tags(db, sample_event)

    async def test_tags_whose_words_all_appear_in_the_event_text(self, db, sample_event):
        await _fill(db)
        event = sample_event.model_copy(update={
            "subject": sample_event.subject.model_copy(update={"title": "Integration test fails on deploy"}),
            "content": sample_event.content.model_copy(update={"body": "The NIGHTLY build broke."}),
        })
        for name in ("integration-test", "deploy", "nightly-build", "deploy-prod", "integrations"):
            await _tag(db, name)
        await db.commit()

        selected = await select_prompt_tags(db, event)

        # Both words of each two-word tag appear in the title or body. Matching
        # ignores case. Names with more words come first.
        assert selected[:3] == ["integration-test", "nightly-build", "deploy"]
        # "prod" does not appear, and "integrations" is a different word.
        assert "deploy-prod" not in selected
        assert "integrations" not in selected

    async def test_text_matches_are_capped(self, db, sample_event):
        await _fill(db)
        words = [f"word{i}" for i in range(PROMPT_TAGS_TEXT_MATCH_MAX + 10)]
        event = sample_event.model_copy(update={
            "content": sample_event.content.model_copy(update={"body": " ".join(words)}),
        })
        for w in words:
            await _tag(db, w)
        await db.commit()

        selected = await select_prompt_tags(db, event)

        assert len([t for t in selected if t.startswith("word")]) == PROMPT_TAGS_TEXT_MATCH_MAX

    async def test_tags_usually_given_to_the_same_actor(self, db, sample_event):
        await _fill(db)
        # sample_event's actor is sarah@company.com; insert_test_event uses the same email.
        await insert_test_card(db, "card_s1", "evt_s1", entity_id="jira:ticket:S-1")
        await insert_test_card(db, "card_s2", "evt_s2", entity_id="jira:ticket:S-2")
        await insert_test_card(db, "card_b1", "evt_b1", entity_id="jira:ticket:B-1")
        await db.execute("UPDATE events SET actor_email = 'bob@company.com' WHERE event_id = 'evt_b1'")
        await _assign(db, "sarah-usual", "card_s1", days_ago=90)
        await _assign(db, "sarah-usual", "card_s2", days_ago=90)
        await _assign(db, "sarah-once", "card_s1", days_ago=90)
        await _assign(db, "bob-only", "card_b1", days_ago=90)
        await db.commit()

        selected = await select_prompt_tags(db, sample_event)

        # Most used first.
        assert selected[:2] == ["sarah-usual", "sarah-once"]
        assert "bob-only" not in selected

    async def test_actor_without_email_is_matched_by_name(self, db, sample_event):
        await _fill(db)
        event = sample_event.model_copy(update={
            "actor": sample_event.actor.model_copy(update={"email": "", "name": "Build Bot"}),
        })
        await insert_test_event(db, "evt_bot", actor_name="Build Bot", actor_email="")
        await insert_test_card(db, "card_bot", "evt_bot", entity_id="jira:ticket:BOT-1")
        await _assign(db, "bot-tag", "card_bot", days_ago=90)
        await db.commit()

        assert "bot-tag" in await select_prompt_tags(db, event)

    async def test_most_used_recent_tags(self, db, sample_event):
        await _fill(db)
        for i in range(3):
            await insert_test_card(db, f"card_{i}", f"evt_{i}", entity_id=f"jira:ticket:X-{i}")
            await db.execute(
                "UPDATE events SET actor_email = 'bob@company.com' WHERE event_id = ?", (f"evt_{i}",)
            )
        await _assign(db, "recent-popular", "card_0", days_ago=2)
        await _assign(db, "recent-popular", "card_1", days_ago=1)
        await _assign(db, "recent-once", "card_2", days_ago=3)
        await db.commit()
        selected = await select_prompt_tags(db, sample_event)
        assert [t for t in selected if t.startswith("recent")] == ["recent-popular", "recent-once"]

    async def test_old_assignments_do_not_count_as_recent(self, db, sample_event):
        await _fill(db)
        await insert_test_card(db, "card_old", "evt_old", entity_id="jira:ticket:OLD-1")
        await db.execute("UPDATE events SET actor_email = 'bob@company.com' WHERE event_id = 'evt_old'")
        await _assign(db, "last-year", "card_old", days_ago=200)
        await insert_test_card(db, "card_new", "evt_new", entity_id="jira:ticket:NEW-1")
        await db.execute("UPDATE events SET actor_email = 'bob@company.com' WHERE event_id = 'evt_new'")
        await _assign(db, "this-week", "card_new", days_ago=1)
        await db.commit()

        selected = await select_prompt_tags(db, sample_event)

        assert "this-week" in selected
        assert "last-year" not in selected

    async def test_no_duplicates_when_sources_overlap(self, db, sample_event):
        await _fill(db)
        await insert_test_card(db, "card_old", "evt_old", entity_id=sample_event.entity_id)
        # "paymentservice" is on the entity, matches the title, belongs to the
        # same actor and is recent.
        await _assign(db, "paymentservice", "card_old", days_ago=1)
        await db.commit()

        selected = await select_prompt_tags(db, sample_event)

        assert selected.count("paymentservice") == 1
        assert selected[0] == "paymentservice"


@pytest.mark.asyncio
class TestStagerUsesTheSelection:
    async def test_run_stager_sends_the_selected_tags(
        self, db, sample_event, sample_router_output_engineer, mock_llm_stager,
    ):
        from laya.pipeline.stager import run_stager

        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.pipeline.stager.select_prompt_tags", new_callable=AsyncMock,
                   return_value=["payments", "incident"]) as select:
            await run_stager(sample_event, sample_router_output_engineer)

        select.assert_awaited_once()
        user_message = mock_llm_stager.call_args.kwargs["messages"][1]["content"]
        assert "[EXISTING TAGS]\npayments, incident\n" in user_message

    async def test_run_stager_survives_a_failing_selection(
        self, db, sample_event, sample_router_output_engineer, mock_llm_stager,
    ):
        from laya.pipeline.stager import run_stager

        with patch("laya.pipeline.related_context.memory_search", new_callable=AsyncMock, return_value=[]), \
             patch("laya.pipeline.stager.select_prompt_tags", new_callable=AsyncMock,
                   side_effect=RuntimeError("db is busy")):
            card = await run_stager(sample_event, sample_router_output_engineer)

        assert card.header
        user_message = mock_llm_stager.call_args.kwargs["messages"][1]["content"]
        assert "[EXISTING TAGS]" not in user_message


class TestTagBlockWording:
    def test_block_does_not_claim_to_be_complete(self, sample_event, sample_router_output_engineer):
        messages = build_stager_messages(
            sample_event, sample_router_output_engineer, existing_tags=["payments"],
        )
        user_message = messages[1]["content"]
        assert "[EXISTING TAGS]\npayments\n" in user_message
        assert "existing tags that may fit this event" in user_message
        assert "You may also suggest new tags." in user_message

    def test_no_block_without_tags(self, sample_event, sample_router_output_engineer):
        messages = build_stager_messages(sample_event, sample_router_output_engineer, existing_tags=[])
        assert "[EXISTING TAGS]" not in messages[1]["content"]

    def test_limits_are_small(self):
        """The list must stay short however many tags exist. The sum of the
        per-source limits is the upper bound of its length, system tags and
        same-entity tags aside."""
        bound = (
            tags_module.PROMPT_TAGS_RECENT_TOP
            + tags_module.PROMPT_TAGS_ACTOR_TOP
            + tags_module.PROMPT_TAGS_TEXT_MATCH_MAX
        )
        assert bound <= 300
