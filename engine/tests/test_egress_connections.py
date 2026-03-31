"""Tests for the Connection Broker — credential management."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from laya.egress.connections import (
    create_connection,
    list_all_connections,
    remove_connection,
    test_connection as check_connection,
    _validate_credentials,
)
from laya.egress.models import ConnectionResult


class TestValidateCredentials:
    @pytest.mark.asyncio
    async def test_jira_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("jira", {
                "domain": "company.atlassian.net",
                "email": "user@co.com",
                "apiToken": "token123",
            })
            assert valid is True
            assert error is None

    @pytest.mark.asyncio
    async def test_jira_invalid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("jira", {
                "domain": "company.atlassian.net",
                "email": "user@co.com",
                "apiToken": "bad_token",
            })
            assert valid is False
            assert "Invalid" in error or "credentials" in error.lower()

    @pytest.mark.asyncio
    async def test_jira_missing_fields(self):
        valid, error = await _validate_credentials("jira", {"domain": "x"})
        assert valid is False
        assert "Missing" in error

    @pytest.mark.asyncio
    async def test_github_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("github", {"accessToken": "ghp_abc"})
            assert valid is True

    @pytest.mark.asyncio
    async def test_slack_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("slack", {"accessToken": "xoxb-abc"})
            assert valid is True

    @pytest.mark.asyncio
    async def test_slack_invalid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": False, "error": "invalid_auth"}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("slack", {"accessToken": "bad"})
            assert valid is False
            assert "invalid_auth" in error

    @pytest.mark.asyncio
    async def test_bitbucket_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("bitbucket", {
                "username": "user",
                "appPassword": "pw",
            })
            assert valid is True

    @pytest.mark.asyncio
    async def test_oauth_platform_auto_valid(self):
        valid, error = await _validate_credentials("gmail", {})
        assert valid is True  # OAuth tokens pre-validated during flow

    @pytest.mark.asyncio
    async def test_unknown_platform_passes(self):
        valid, error = await _validate_credentials("notion", {"apiKey": "x"})
        assert valid is True


class TestCreateConnection:
    @pytest.mark.asyncio
    async def test_create_connection_success(self, db):
        """Test successful connection creation with mocked validation and keychain."""
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="n8n_cred_1"):
                    result = await create_connection("jira", {"email": "x", "apiToken": "y", "domain": "z"})

                    assert result.status == "connected"
                    assert result.connection_id is not None
                    assert "comment" in result.capabilities

                    # Verify DB record
                    rows = await db.execute_fetchall("SELECT * FROM egress_connections")
                    assert len(rows) == 1
                    assert rows[0]["platform"] == "jira"
                    assert rows[0]["status"] == "connected"

    @pytest.mark.asyncio
    async def test_create_connection_validation_failure(self, db):
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(False, "Bad token")):
            result = await create_connection("jira", {"email": "x"})
            assert result.status == "failed"
            assert "Bad token" in result.error

    @pytest.mark.asyncio
    async def test_create_connection_unknown_platform(self, db):
        result = await create_connection("totally_unknown", {})
        assert result.status == "failed"
        assert "Unknown" in result.error


class TestListConnections:
    @pytest.mark.asyncio
    async def test_list_empty(self, db):
        conns = await list_all_connections()
        assert conns == []

    @pytest.mark.asyncio
    async def test_list_after_create(self, db):
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    await create_connection("github", {"accessToken": "ghp_abc"}, name="GitHub Main")

        conns = await list_all_connections()
        assert len(conns) == 1
        assert conns[0].platform == "github"
        assert conns[0].name == "GitHub Main"
        assert "comment" in conns[0].capabilities


class TestRemoveConnection:
    @pytest.mark.asyncio
    async def test_remove_connection(self, db):
        # Create first
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    result = await create_connection("slack", {"accessToken": "xoxb"})

        # Verify exists
        conns = await list_all_connections()
        assert len(conns) == 1

        # Remove
        with patch("laya.egress.connections._remove_from_keychain"):
            with patch("laya.integrations.n8n_client.delete_credential", new_callable=AsyncMock):
                await remove_connection(result.connection_id)

        # Verify gone
        conns = await list_all_connections()
        assert len(conns) == 0


class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_revalidate_connection(self, db):
        # Create
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    result = await create_connection("github", {"accessToken": "ghp_abc"})

        # Test it
        with patch("laya.egress.connections._get_from_keychain", return_value={"accessToken": "ghp_abc"}):
            with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
                valid, error = await check_connection(result.connection_id)
                assert valid is True

        # Verify status updated
        conns = await list_all_connections()
        assert conns[0].status == "connected"
        assert conns[0].last_validated_at is not None
