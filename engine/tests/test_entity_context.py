# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the entity-level agent context builder (CONTEXT.md + prompts)."""

import pytest

from laya.agents.entity_context import (
    build_entity_context_markdown,
    build_entity_resume_prompt,
)
from laya.models.card import PROCESSING_PLACEHOLDER_SUMMARY
from tests.conftest import insert_test_card

ENTITY = "jira:ticket:CTX-1"


class TestBuildEntityResumePrompt:
    """The resume message always points the agent back at CONTEXT.md (issue #43)."""

    def test_no_prompt_uses_default_with_reminder(self):
        text = build_entity_resume_prompt(None)
        assert text.startswith("Continue working.")
        assert "CONTEXT.md" in text

    def test_empty_prompt_uses_default(self):
        assert build_entity_resume_prompt("") == build_entity_resume_prompt(None)
        assert build_entity_resume_prompt("   ") == build_entity_resume_prompt(None)

    def test_custom_prompt_keeps_reminder(self):
        text = build_entity_resume_prompt("What is the status of the migration?")
        assert text.startswith("What is the status of the migration?")
        assert "CONTEXT.md" in text
        assert "Continue working." not in text

    def test_custom_prompt_is_stripped(self):
        text = build_entity_resume_prompt("  do the thing  \n")
        assert text.startswith("do the thing\n")


@pytest.mark.asyncio
class TestContextMarkdownPlaceholder:
    async def test_placeholder_summary_is_flagged_as_processing(self, db):
        """A card still mid-pipeline is described as unprocessed, not copied verbatim."""
        await insert_test_card(
            db, "card_proc", "evt_proc", entity_id=ENTITY,
            summary=PROCESSING_PLACEHOLDER_SUMMARY,
        )
        md = await build_entity_context_markdown(ENTITY)
        assert PROCESSING_PLACEHOLDER_SUMMARY not in md
        assert "still processing this card" in md

    async def test_real_summary_is_included(self, db):
        await insert_test_card(
            db, "card_done", "evt_done", entity_id=ENTITY,
            summary="Null pointer in checkout flow",
        )
        md = await build_entity_context_markdown(ENTITY)
        assert "Null pointer in checkout flow" in md
        assert "still processing this card" not in md
