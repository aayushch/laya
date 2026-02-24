"""Integration tests for the Team and Rules REST API endpoints."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.main import app


@pytest.fixture
def team_storage(tmp_path):
    """Temporary team.json storage."""
    team_file = tmp_path / "team.json"
    team_file.write_text(json.dumps({"members": []}))

    def _load():
        return json.loads(team_file.read_text())

    def _save(data):
        team_file.write_text(json.dumps(data, indent=2))

    with patch("laya.api.team.load_team", side_effect=_load):
        with patch("laya.api.team.save_team", side_effect=_save):
            yield team_file


@pytest.fixture
def rules_storage(tmp_path):
    """Temporary rules.json storage."""
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"rules": []}))

    def _load():
        return json.loads(rules_file.read_text())

    def _save(data):
        rules_file.write_text(json.dumps(data, indent=2))

    with patch("laya.api.rules_api.load_rules", side_effect=_load):
        with patch("laya.api.rules_api.save_rules", side_effect=_save):
            yield rules_file


@pytest.mark.asyncio
async def test_get_team_returns_empty(team_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/team")
        assert resp.status_code == 200
        assert resp.json() == {"members": []}


@pytest.mark.asyncio
async def test_put_team_valid(team_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        team = {
            "members": [
                {"name": "Alice", "email": "alice@co.com", "role": "teammate", "notes": ""}
            ]
        }
        resp = await client.put("/team", json=team)
        assert resp.status_code == 200
        assert resp.json()["member_count"] == 1

        # Verify persistence
        resp2 = await client.get("/team")
        assert len(resp2.json()["members"]) == 1
        assert resp2.json()["members"][0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_put_team_invalid_role(team_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        team = {
            "members": [
                {"name": "Bob", "email": "bob@co.com", "role": "invalid_role", "notes": ""}
            ]
        }
        resp = await client.put("/team", json=team)
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_rules_returns_empty(rules_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/rules")
        assert resp.status_code == 200
        assert resp.json() == {"rules": []}


@pytest.mark.asyncio
async def test_put_rules_valid(rules_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rules = {
            "rules": [
                {
                    "name": "Test rule",
                    "enabled": True,
                    "condition": {"field": "actor.email", "operator": "contains", "value": "bot"},
                    "action": "drop",
                }
            ]
        }
        resp = await client.put("/rules", json=rules)
        assert resp.status_code == 200
        assert resp.json()["rule_count"] == 1

        resp2 = await client.get("/rules")
        assert len(resp2.json()["rules"]) == 1


@pytest.mark.asyncio
async def test_put_rules_invalid_operator(rules_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rules = {
            "rules": [
                {
                    "name": "Bad rule",
                    "enabled": True,
                    "condition": {"field": "actor.email", "operator": "invalid_op", "value": "x"},
                    "action": "drop",
                }
            ]
        }
        resp = await client.put("/rules", json=rules)
        assert resp.status_code == 422
