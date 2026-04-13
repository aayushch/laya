"""Tests for Entity Resolution layers 2 & 3."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.pipeline.entity_resolution import (
    _get_semantic_threshold,
    confirm_entity_link,
    resolve_semantic_entities,
)

SEMANTIC_THRESHOLD = _get_semantic_threshold()


@pytest.mark.asyncio
class TestSemanticResolution:
    async def test_finds_semantic_matches(self, db):
        """Layer 2 finds cross-references below the distance threshold."""
        # Insert an existing entity
        await db.execute(
            "INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs, link_method, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ent_existing", "ticket", "BUG-999", '{"jira": ["BUG-999"]}', "explicit", 1.0),
        )
        await db.commit()

        # Mock memory_search to return a result referencing BUG-999
        mock_results = [
            {
                "id": "doc_1",
                "document": "Related to BUG-999",
                "metadata": {"entity_refs": "BUG-999", "source_platform": "jira"},
                "distance": 0.2,  # below threshold
            }
        ]
        with patch("laya.pipeline.entity_resolution.memory_search", new_callable=AsyncMock, return_value=mock_results):
            matches = await resolve_semantic_entities(
                "card_test", "Fix NPE in PaymentService", ["BUG-1234"]
            )

        assert len(matches) == 1
        assert matches[0]["entity_a"] == "BUG-1234"
        assert matches[0]["entity_b"] == "BUG-999"
        assert matches[0]["link_method"] == "semantic"
        assert matches[0]["confidence"] > 0.5

    async def test_skips_above_threshold(self, db):
        """Layer 2 skips results above the distance threshold."""
        mock_results = [
            {
                "id": "doc_1",
                "document": "Unrelated content",
                "metadata": {"entity_refs": "BUG-999", "source_platform": "jira"},
                "distance": 0.8,  # above threshold
            }
        ]
        with patch("laya.pipeline.entity_resolution.memory_search", new_callable=AsyncMock, return_value=mock_results):
            matches = await resolve_semantic_entities(
                "card_test", "Fix NPE", ["BUG-1234"]
            )

        assert len(matches) == 0

    async def test_skips_self_reference(self, db):
        """Layer 2 skips when entity_refs contain the same entity being searched."""
        mock_results = [
            {
                "id": "doc_1",
                "document": "Same entity",
                "metadata": {"entity_refs": "BUG-1234", "source_platform": "jira"},
                "distance": 0.1,
            }
        ]
        with patch("laya.pipeline.entity_resolution.memory_search", new_callable=AsyncMock, return_value=mock_results):
            matches = await resolve_semantic_entities(
                "card_test", "Fix NPE", ["BUG-1234"]
            )

        # Should not match because entity_refs contain the searched entity
        assert len(matches) == 0

    async def test_handles_memory_search_failure(self, db):
        """Layer 2 handles memory_search errors gracefully."""
        with patch("laya.pipeline.entity_resolution.memory_search", new_callable=AsyncMock, side_effect=Exception("ChromaDB down")):
            matches = await resolve_semantic_entities(
                "card_test", "Fix NPE", ["BUG-1234"]
            )

        assert len(matches) == 0


@pytest.mark.asyncio
class TestLLMConfirmation:
    async def test_confirms_match(self, db):
        """Layer 3 confirms entity match and updates link_method."""
        # Insert a semantic link to be upgraded
        await db.execute(
            "INSERT INTO entities (entity_id, entity_type, canonical_name, platform_refs, link_method, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ent_link", "cross_reference", "BUG-1234 <-> BUG-999", '{}', "semantic", 0.8),
        )
        await db.commit()

        from tests.conftest import _make_mock_llm_response
        mock_resp = _make_mock_llm_response({"match": True, "reasoning": "Same bug, different ID"})

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "test-model"}}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await confirm_entity_link("BUG-1234", "BUG-999")

        assert result is True

        # Verify DB updated
        async with db.execute(
            "SELECT link_method, confidence FROM entities WHERE canonical_name = ?",
            ("BUG-1234 <-> BUG-999",),
        ) as cursor:
            row = await cursor.fetchone()

        assert row["link_method"] == "llm_confirmed"
        assert row["confidence"] == 1.0

    async def test_rejects_non_match(self, db):
        """Layer 3 rejects non-matching entities."""
        from tests.conftest import _make_mock_llm_response
        mock_resp = _make_mock_llm_response({"match": False, "reasoning": "Different entities"})

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "test-model"}}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await confirm_entity_link("BUG-1234", "PROJ-567")

        assert result is False

    async def test_handles_llm_failure(self, db):
        """Layer 3 returns False on LLM error."""
        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("API down")):
            with patch("laya.llm.client.load_settings", return_value={"models": {"router": "test-model"}}):
                with patch("laya.pipeline.queue.get_model_timeout", return_value=120):
                    with patch("laya.pipeline.queue.get_llm_retries", return_value=1):
                        result = await confirm_entity_link("BUG-1234", "BUG-999")

        assert result is False
