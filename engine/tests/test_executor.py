"""Tests for the action execution service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from laya.egress.models import EgressResult
from tests.conftest import insert_test_card, insert_test_event


@pytest.mark.asyncio
class TestExecutor:
    async def test_execute_success(self, db):
        """Successful execution updates card to done and stores action_log."""
        await insert_test_card(db, "card_exec", "evt_exec", status="pending")

        mock_egress_result = EgressResult(
            success=True,
            result_url="https://jira.example.com/BUG-1234",
            result_data={"url": "https://jira.example.com/BUG-1234"},
        )

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock) as mock_bc:
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_exec", "act_1")

        assert result["status"] == "done"
        assert result["result_url"] == "https://jira.example.com/BUG-1234"
        assert result["error"] is None

        # Verify card status in DB
        rows = await db.execute_fetchall(
            "SELECT status FROM action_cards WHERE card_id = ?", ("card_exec",)
        )
        assert rows[0]["status"] == "done"

        # Verify action_log entry
        log_rows = await db.execute_fetchall(
            "SELECT * FROM action_log WHERE card_id = ?", ("card_exec",)
        )
        assert len(log_rows) == 1
        assert log_rows[0]["result_status"] == "done"

    async def test_execute_failure(self, db):
        """Failed egress response updates card to failed with error message."""
        await insert_test_card(db, "card_fail", "evt_fail", status="pending")

        mock_egress_result = EgressResult(
            success=False,
            error="Jira auth failed",
        )

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_fail", "act_1")

        assert result["status"] == "failed"
        assert result["error"] == "Jira auth failed"

        rows = await db.execute_fetchall(
            "SELECT status FROM action_cards WHERE card_id = ?", ("card_fail",)
        )
        assert rows[0]["status"] == "failed"

    async def test_execute_card_not_found(self, db):
        """Raises ValueError when card does not exist."""
        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="Card not found"):
                await execute_action("card_ghost", "act_1")

    async def test_execute_wrong_status(self, db):
        """Raises ValueError when card status is not in the allowed set."""
        await insert_test_card(db, "card_done", "evt_done", status="done")

        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="must be"):
                await execute_action("card_done", "act_1")

    async def test_execute_action_not_found(self, db):
        """Raises ValueError when action_id does not match any suggested action."""
        await insert_test_card(db, "card_noact", "evt_noact", status="pending")

        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="Action.*not found"):
                await execute_action("card_noact", "act_nonexistent")

    async def test_execute_modifications_merged(self, db):
        """User modifications are merged into the action payload."""
        await insert_test_card(db, "card_mod", "evt_mod", status="pending")

        captured_request = {}

        async def capture_egress(request):
            captured_request.update({"payload": request.payload})
            return EgressResult(success=True, result_data={})

        mods = {"body": "Updated comment text"}

        with patch("laya.egress.route_and_execute", side_effect=capture_egress):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                await execute_action("card_mod", "act_1", modifications=mods)

        # Modifications should be merged into payload
        assert captured_request["payload"]["body"] == "Updated comment text"

    async def test_execute_broadcasts_twice(self, db):
        """Broadcasts card_updated twice: once for 'executing', once for final status."""
        await insert_test_card(db, "card_bc", "evt_bc", status="pending")

        mock_egress_result = EgressResult(success=True, result_data={})

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock) as mock_bc:
                from laya.pipeline.executor import execute_action

                await execute_action("card_bc", "act_1")

        assert mock_bc.call_count == 2
        # First broadcast: executing
        first_msg = mock_bc.call_args_list[0].args[0]
        assert first_msg["payload"]["status"] == "executing"
        # Second broadcast: done
        second_msg = mock_bc.call_args_list[1].args[0]
        assert second_msg["payload"]["status"] == "done"

    async def test_execute_from_ready_status(self, db):
        """Execution works from 'ready' status."""
        await insert_test_card(db, "card_rdy", "evt_rdy", status="ready")

        mock_egress_result = EgressResult(success=True, result_data={})

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_rdy", "act_1")

        assert result["status"] == "done"


    async def test_execute_from_failed_status(self, db):
        """Execution works from 'failed' status (retry scenario)."""
        await insert_test_card(db, "card_fail2", "evt_fail2", status="failed")

        mock_egress_result = EgressResult(success=True, result_data={})

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_fail2", "act_1")

        assert result["status"] == "done"

    async def test_execute_from_agent_running_status(self, db):
        """Execution works from 'agent_running' status — users can invoke actions while agent is active."""
        await insert_test_card(db, "card_agrun", "evt_agrun", status="agent_running")

        mock_egress_result = EgressResult(success=True, result_data={})

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_agrun", "act_1")

        assert result["status"] == "done"
