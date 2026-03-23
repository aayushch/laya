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
