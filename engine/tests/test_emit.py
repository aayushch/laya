# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the EMIT pipeline step."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.models.card import ActionCardData, StagedOutput, SuggestedAction
from laya.pipeline.emit import run_emit
from tests.conftest import insert_test_event


def _make_stager_output() -> ActionCardData:
    """Create a sample stager output for testing."""
    return ActionCardData(
        header="Fix NPE in PaymentService",
        summary="NPE found in PaymentService.java when processing null customer IDs.",
        intelligence_report=[
            "NPE at line 42",
            "Root cause: null customer ID",
            "Similar bug fixed in OrderService",
        ],
        staged_output=StagedOutput(type="code_fix", content="Add null check"),
        suggested_actions=[
            SuggestedAction(
                action_id="act_1",
                label="Post Comment",
                action_type="comment",
                target_platform="jira",
                payload={"body": "Fix identified"},
            )
        ],
        privacy_tier=2,
    )


@pytest.mark.asyncio
class TestEmit:
    async def test_creates_card_in_db(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """run_emit inserts a card into action_cards table."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await run_emit(
                            sample_event, sample_router_output_engineer, stager_output,
                        )

        assert card_id.startswith("card_")

        # Verify DB row
        rows = await db.execute_fetchall(
            "SELECT card_id, header, priority, persona, status FROM action_cards WHERE card_id = ?",
            (card_id,),
        )
        assert len(rows) == 1
        assert rows[0]["header"] == "Fix NPE in PaymentService"
        assert rows[0]["priority"] == "HIGH"
        assert rows[0]["persona"] == "ENGINEER"
        assert rows[0]["status"] == "ready"

    async def test_embeds_in_chromadb(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """run_emit calls embed_document with card summary."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock) as mock_embed:
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        await run_emit(sample_event, sample_router_output_engineer, stager_output)

        mock_embed.assert_called_once()
        call_kwargs = mock_embed.call_args
        assert "card_summary" in str(call_kwargs)

    async def test_broadcasts_card_created(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """run_emit broadcasts card_created via WebSocket."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock) as mock_broadcast:
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await run_emit(
                            sample_event, sample_router_output_engineer, stager_output,
                        )

        # broadcast is called at least once for card_created
        mock_broadcast.assert_called()
        # Find the card_created call
        card_created_calls = [
            c for c in mock_broadcast.call_args_list
            if c[0][0].get("type") == "card_created"
        ]
        assert len(card_created_calls) == 1
        broadcast_msg = card_created_calls[0][0][0]
        assert broadcast_msg["card_id"] == card_id
        assert broadcast_msg["payload"]["priority"] == "HIGH"

    async def test_detects_has_workspace(
        self, db, sample_event, sample_router_output_engineer,
        sample_worker_result,
    ):
        """run_emit sets has_workspace=True when workers have session_id."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await run_emit(
                            sample_event, sample_router_output_engineer, stager_output,
                            worker_results=[sample_worker_result],
                        )

        rows = await db.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?", (card_id,)
        )
        assert bool(rows[0]["has_workspace"]) is True

    async def test_no_workspace_without_session(
        self, db, sample_event, sample_router_output_comms,
        sample_worker_result_no_session,
    ):
        """run_emit sets has_workspace=False when no worker has session_id."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await run_emit(
                            sample_event, sample_router_output_comms, stager_output,
                            worker_results=[sample_worker_result_no_session],
                        )

        rows = await db.execute_fetchall(
            "SELECT has_workspace FROM action_cards WHERE card_id = ?", (card_id,)
        )
        assert bool(rows[0]["has_workspace"]) is False

    async def test_followup_card_gets_thread_context(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """A follow-up card joining an existing entity group gets a 'Thread so far:'
        clause in its embedded text (Contextual Embeddings); the first card in the
        group does not — it is already self-contained."""
        await insert_test_event(db, sample_event.event_id)
        # Second event, same subject (jira:ticket:BUG-1234) -> same entity_id.
        second_event = sample_event.model_copy(update={"event_id": "evt_test-001b"})
        await insert_test_event(db, second_event.event_id)

        first_output = _make_stager_output()
        update_output = ActionCardData(
            header="BUG-1234 resolved as Fixed",
            summary="Resolved as Fixed.",
            intelligence_report=["Closed by Alice"],
            staged_output=StagedOutput(type="status_update", content="Resolved"),
            suggested_actions=[],
            privacy_tier=2,
        )

        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock) as mock_embed, \
             patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]), \
             patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock), \
             patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock), \
             patch("laya.pipeline.context_grouping.resolve_context_group", new_callable=AsyncMock, return_value=None), \
             patch("laya.pipeline.group_summary.trigger_group_summary_update", new_callable=AsyncMock):
            await run_emit(sample_event, sample_router_output_engineer, first_output)
            await run_emit(second_event, sample_router_output_engineer, update_output)

        assert mock_embed.call_count == 2
        first_text = mock_embed.call_args_list[0].kwargs["text"]
        second_text = mock_embed.call_args_list[1].kwargs["text"]

        # First card: no thread-context clause (self-contained).
        assert "Thread so far:" not in first_text
        # Follow-up card: carries the thread referent. With no rolling group summary
        # yet (tier 1), it falls back to the prior card's header (tier 2).
        assert "Thread so far:" in second_text
        assert "Fix NPE in PaymentService" in second_text

    async def test_writes_audit_log(
        self, db, sample_event, sample_router_output_engineer,
    ):
        """run_emit writes an entry to audit_log."""
        await insert_test_event(db, sample_event.event_id)

        stager_output = _make_stager_output()
        with patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock):
            with patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock, return_value=[]):
                with patch("laya.pipeline.emit.manager.broadcast", new_callable=AsyncMock):
                    with patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock):
                        card_id = await run_emit(
                            sample_event, sample_router_output_engineer, stager_output,
                        )

        rows = await db.execute_fetchall(
            "SELECT step, card_id FROM audit_log WHERE card_id = ?", (card_id,)
        )
        assert len(rows) == 1
        assert rows[0]["step"] == "emit"
