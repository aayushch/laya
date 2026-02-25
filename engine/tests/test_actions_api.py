"""Tests for the Actions REST API."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_cards_api import _insert_test_card


@pytest.mark.asyncio
class TestActionsAPI:
    async def test_execute_action_returns_result(self, db_m4):
        """POST /actions/execute returns execution result."""
        await _insert_test_card(db_m4, "card_api", "evt_api", status="pending")

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
            with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
                from laya.main import app

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/actions/execute",
                        json={
                            "card_id": "card_api",
                            "action_id": "act_1",
                        },
                    )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["card_id"] == "card_api"

    async def test_execute_action_400_on_bad_card(self, db_m4):
        """POST /actions/execute returns 400 for non-existent card."""
        with patch("laya.pipeline.executor.manager.broadcast", new_callable=AsyncMock):
            from laya.main import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/actions/execute",
                    json={
                        "card_id": "card_ghost",
                        "action_id": "act_1",
                    },
                )

        assert resp.status_code == 400
        assert "Card not found" in resp.json()["detail"]
