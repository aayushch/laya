"""Tests for the action execution service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_cards_api import _insert_test_card


@pytest.mark.asyncio
class TestExecutor:
    async def test_execute_success(self, db_m4):
        """Successful execution updates card to completed and stores action_log."""
        await _insert_test_card(db_m4, "card_exec", "evt_exec", status="pending")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "result": {"url": "https://jira.example.com/BUG-1234"},
        }
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.pipeline.executor.httpx.AsyncClient", return_value=mock_client):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock) as mock_bc:
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_exec", "act_1")

        assert result["status"] == "completed"
        assert result["result_url"] == "https://jira.example.com/BUG-1234"
        assert result["error"] is None

        # Verify card status in DB
        rows = await db_m4.execute_fetchall(
            "SELECT status FROM action_cards WHERE card_id = ?", ("card_exec",)
        )
        assert rows[0]["status"] == "completed"

        # Verify action_log entry
        log_rows = await db_m4.execute_fetchall(
            "SELECT * FROM action_log WHERE card_id = ?", ("card_exec",)
        )
        assert len(log_rows) == 1
        assert log_rows[0]["result_status"] == "completed"

    async def test_execute_failure(self, db_m4):
        """Failed n8n response updates card to failed with error message."""
        await _insert_test_card(db_m4, "card_fail", "evt_fail", status="pending")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": False, "error": "Jira auth failed"}
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.pipeline.executor.httpx.AsyncClient", return_value=mock_client):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_fail", "act_1")

        assert result["status"] == "failed"
        assert result["error"] == "Jira auth failed"

        rows = await db_m4.execute_fetchall(
            "SELECT status FROM action_cards WHERE card_id = ?", ("card_fail",)
        )
        assert rows[0]["status"] == "failed"

    async def test_execute_card_not_found(self, db_m4):
        """Raises ValueError when card does not exist."""
        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="Card not found"):
                await execute_action("card_ghost", "act_1")

    async def test_execute_wrong_status(self, db_m4):
        """Raises ValueError when card status is not pending or approved."""
        await _insert_test_card(db_m4, "card_done", "evt_done", status="completed")

        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="must be 'pending' or 'approved'"):
                await execute_action("card_done", "act_1")

    async def test_execute_action_not_found(self, db_m4):
        """Raises ValueError when action_id does not match any suggested action."""
        await _insert_test_card(db_m4, "card_noact", "evt_noact", status="pending")

        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.pipeline.executor import execute_action

            with pytest.raises(ValueError, match="Action.*not found"):
                await execute_action("card_noact", "act_nonexistent")

    async def test_execute_modifications_merged(self, db_m4):
        """User modifications are merged into the action payload."""
        await _insert_test_card(db_m4, "card_mod", "evt_mod", status="pending")

        posted_payload = {}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "result": {}}
        mock_resp.status_code = 200

        async def capture_post(url, json=None):
            nonlocal posted_payload
            posted_payload = json
            return mock_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post

        mods = {"body": "Updated comment text"}

        with patch("laya.pipeline.executor.httpx.AsyncClient", return_value=mock_client):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                await execute_action("card_mod", "act_1", modifications=mods)

        # Modifications should be merged into payload
        assert posted_payload["payload"]["body"] == "Updated comment text"

    async def test_execute_broadcasts_twice(self, db_m4):
        """Broadcasts card_updated twice: once for 'executing', once for final status."""
        await _insert_test_card(db_m4, "card_bc", "evt_bc", status="pending")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "result": {}}
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.pipeline.executor.httpx.AsyncClient", return_value=mock_client):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock) as mock_bc:
                from laya.pipeline.executor import execute_action

                await execute_action("card_bc", "act_1")

        assert mock_bc.call_count == 2
        # First broadcast: executing
        first_msg = mock_bc.call_args_list[0].args[0]
        assert first_msg["payload"]["status"] == "executing"
        # Second broadcast: completed
        second_msg = mock_bc.call_args_list[1].args[0]
        assert second_msg["payload"]["status"] == "completed"

    async def test_execute_from_approved_status(self, db_m4):
        """Execution works from 'approved' status too."""
        await _insert_test_card(db_m4, "card_app", "evt_app", status="approved")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "result": {}}
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.pipeline.executor.httpx.AsyncClient", return_value=mock_client):
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.pipeline.executor import execute_action

                result = await execute_action("card_app", "act_1")

        assert result["status"] == "completed"
