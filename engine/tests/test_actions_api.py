"""Tests for the Actions REST API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.egress.models import EgressResult
from tests.conftest import insert_test_card


@pytest.mark.asyncio
class TestActionsAPI:
    async def test_execute_action_returns_result(self, db):
        """POST /actions/execute returns execution result."""
        await insert_test_card(db, "card_api", "evt_api", status="pending")

        mock_egress_result = EgressResult(
            success=True,
            result_url="https://jira.example.com/BUG-1234",
            result_data={"url": "https://jira.example.com/BUG-1234"},
        )

        with patch("laya.egress.route_and_execute", new_callable=AsyncMock, return_value=mock_egress_result):
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
