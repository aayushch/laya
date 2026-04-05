"""Tests for platform connections API (n8n credential management)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.integrations.n8n_client import N8nApiError, N8nApiKeyMissing
from laya.integrations.platforms import PLATFORMS, SUPPORTED_N8N_TYPES, get_platform_by_n8n_type


class TestPlatformsRegistry:
    """Tests for the platform registry module."""

    def test_all_platforms_have_required_keys(self):
        """Every platform must define label, n8n_type, n8n_node, oauth, fields, category, icon."""
        for key, p in PLATFORMS.items():
            assert "label" in p, f"{key} missing label"
            assert "n8n_type" in p, f"{key} missing n8n_type"
            assert "n8n_node" in p, f"{key} missing n8n_node"
            assert "oauth" in p, f"{key} missing oauth"
            assert "fields" in p, f"{key} missing fields"
            assert "category" in p, f"{key} missing category"
            assert "icon" in p, f"{key} missing icon"

    def test_platform_count(self):
        """Registry should have 12 platforms."""
        assert len(PLATFORMS) == 12

    def test_supported_n8n_types_matches(self):
        """SUPPORTED_N8N_TYPES set matches all platform n8n_types."""
        expected = {p["n8n_type"] for p in PLATFORMS.values()}
        assert SUPPORTED_N8N_TYPES == expected

    def test_get_platform_by_n8n_type_found(self):
        """get_platform_by_n8n_type returns correct platform for known types."""
        result = get_platform_by_n8n_type("jiraSoftwareCloudApi")
        assert result is not None
        assert result[0] == "jira"
        assert result[1]["label"] == "Jira Cloud"

    def test_get_platform_by_n8n_type_not_found(self):
        """get_platform_by_n8n_type returns None for unknown types."""
        assert get_platform_by_n8n_type("unknownApi") is None


@pytest.mark.asyncio
class TestGetPlatformsEndpoint:
    """Tests for GET /connections/platforms."""

    async def test_returns_platforms_registry(self):
        """GET /connections/platforms returns the full platform registry."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/connections/platforms")

        assert resp.status_code == 200
        data = resp.json()
        assert "platforms" in data
        assert "jira" in data["platforms"]
        assert "slack" in data["platforms"]
        assert data["platforms"]["jira"]["n8n_type"] == "jiraSoftwareCloudApi"
        assert len(data["platforms"]["jira"]["fields"]) == 3


@pytest.mark.asyncio
class TestGetConnectionsEndpoint:
    """Tests for GET /connections."""

    async def test_filters_to_supported_types(self):
        """GET /connections returns only credentials with supported n8n types."""
        from laya.main import app

        mock_creds = [
            {"id": "1", "name": "My Jira", "type": "jiraSoftwareCloudApi", "createdAt": "2026-01-01", "updatedAt": "2026-01-02"},
            {"id": "2", "name": "My Slack", "type": "slackApi", "createdAt": "2026-01-01", "updatedAt": "2026-01-02"},
            {"id": "3", "name": "Some Custom", "type": "customUnknownApi", "createdAt": "2026-01-01", "updatedAt": "2026-01-02"},
        ]

        with patch("laya.api.connections_api.list_credentials", new_callable=AsyncMock, return_value=mock_creds):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/connections")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["connections"]) == 2
        ids = {c["id"] for c in data["connections"]}
        assert "1" in ids
        assert "2" in ids
        assert "3" not in ids

    async def test_enriches_with_platform_info(self):
        """GET /connections adds platform key and label to each connection."""
        from laya.main import app

        mock_creds = [
            {"id": "1", "name": "My Jira", "type": "jiraSoftwareCloudApi", "createdAt": "2026-01-01", "updatedAt": "2026-01-02"},
        ]

        with patch("laya.api.connections_api.list_credentials", new_callable=AsyncMock, return_value=mock_creds):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/connections")

        data = resp.json()
        conn = data["connections"][0]
        assert conn["platform"] == "jira"
        assert conn["platform_label"] == "Jira Cloud"

    async def test_returns_422_when_no_api_key(self):
        """GET /connections returns 422 when n8n API key is missing."""
        from laya.main import app

        with patch(
            "laya.api.connections_api.list_credentials",
            new_callable=AsyncMock,
            side_effect=N8nApiKeyMissing("n8n API key not configured"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/connections")

        assert resp.status_code == 422


@pytest.mark.asyncio
class TestCreateConnectionEndpoint:
    """Tests for POST /connections."""

    async def test_creates_credential_via_n8n(self):
        """POST /connections creates credential in n8n with correct body."""
        from laya.main import app

        mock_result = {"id": "42", "name": "My Jira"}

        with patch("laya.api.connections_api.create_credential", new_callable=AsyncMock, return_value=mock_result) as mock_create:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/connections",
                    json={
                        "platform": "jira",
                        "name": "My Jira",
                        "credentials": {
                            "email": "me@co.com",
                            "apiToken": "tok123",
                            "domain": "co.atlassian.net",
                        },
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["id"] == "42"
        assert data["platform"] == "jira"

        mock_create.assert_called_once_with(
            name="My Jira",
            n8n_type="jiraSoftwareCloudApi",
            data={"email": "me@co.com", "apiToken": "tok123", "domain": "co.atlassian.net"},
            node_type="n8n-nodes-base.jira",
        )

    async def test_validates_required_fields(self):
        """POST /connections returns 400 when required credential fields are missing."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/connections",
                json={
                    "platform": "jira",
                    "name": "My Jira",
                    "credentials": {"email": "me@co.com"},  # missing apiToken and domain
                },
            )

        assert resp.status_code == 400
        assert "Missing required fields" in resp.json()["detail"]

    async def test_rejects_oauth_platforms(self):
        """POST /connections returns 400 for OAuth platforms like Gmail."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/connections",
                json={
                    "platform": "gmail",
                    "name": "My Gmail",
                    "credentials": {},
                },
            )

        assert resp.status_code == 400
        assert "OAuth" in resp.json()["detail"]

    async def test_rejects_unknown_platform(self):
        """POST /connections returns 400 for unknown platforms."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/connections",
                json={
                    "platform": "notreal",
                    "name": "Nope",
                    "credentials": {},
                },
            )

        assert resp.status_code == 400
        assert "Unknown platform" in resp.json()["detail"]

    async def test_returns_422_when_no_api_key(self):
        """POST /connections returns 422 when n8n API key is missing."""
        from laya.main import app

        with patch(
            "laya.api.connections_api.create_credential",
            new_callable=AsyncMock,
            side_effect=N8nApiKeyMissing("n8n API key not configured"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/connections",
                    json={
                        "platform": "slack",
                        "name": "My Slack",
                        "credentials": {"accessToken": "xoxb-123"},
                    },
                )

        assert resp.status_code == 422


@pytest.mark.asyncio
class TestDeleteConnectionEndpoint:
    """Tests for DELETE /connections/{id}."""

    async def test_deletes_credential(self):
        """DELETE /connections/{id} forwards to n8n and returns success."""
        from laya.main import app

        with patch("laya.api.connections_api.delete_credential", new_callable=AsyncMock, return_value=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/connections/42")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["id"] == "42"

    async def test_returns_422_when_no_api_key(self):
        """DELETE /connections/{id} returns 422 when n8n API key is missing."""
        from laya.main import app

        with patch(
            "laya.api.connections_api.delete_credential",
            new_callable=AsyncMock,
            side_effect=N8nApiKeyMissing("n8n API key not configured"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/connections/42")

        assert resp.status_code == 422


@pytest.mark.asyncio
class TestConnectionTestEndpoint:
    """Tests for POST /connections/test."""

    async def test_returns_connected(self):
        """POST /connections/test returns connected status on success."""
        from laya.main import app

        with patch(
            "laya.api.connections_api.test_api_access",
            new_callable=AsyncMock,
            return_value={"status": "connected", "message": "n8n API accessible"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/connections/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        assert data["message"] == "n8n API accessible"
