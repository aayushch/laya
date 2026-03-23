"""Tests for the Actions REST API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import insert_test_card


MOCK_N8N_CONFIG = {
    "base_url": "http://localhost:5678",
    "webhooks": {"jira": "jira-executor"},
}


@pytest.mark.asyncio
class TestActionsAPI:
    async def test_execute_action_returns_result(self, db):
        """POST /actions/execute returns execution result."""
        await insert_test_card(db, "card_api", "evt_api", status="pending")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "result": {"url": "https://jira.example.com/BUG-1234"},
        }
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.pipeline.executor.get_client", return_value=mock_client):
            with patch("laya.pipeline.executor.get_n8n_config", return_value=MOCK_N8N_CONFIG):
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
        assert data["status"] == "done"
        assert data["card_id"] == "card_api"

    async def test_execute_action_400_on_bad_card(self, db):
        """POST /actions/execute returns 400 for non-existent card."""
        with patch("laya.pipeline.executor.get_n8n_config", return_value=MOCK_N8N_CONFIG):
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
