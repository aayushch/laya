"""Tests for n8n auto-provisioning (bootstrap)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from laya.integrations.n8n_bootstrap import (
    _create_api_key,
    _create_owner,
    _try_login,
    _wait_for_n8n,
    ensure_n8n_ready,
    import_workflows,
)


@pytest.mark.asyncio
class TestWaitForN8n:
    """Tests for _wait_for_n8n health polling."""

    async def test_returns_true_when_healthy(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _wait_for_n8n("http://localhost:45678", timeout=2.0)

        assert result is True

    async def test_returns_false_on_timeout(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _wait_for_n8n("http://localhost:45678", timeout=1.5)

        assert result is False


@pytest.mark.asyncio
class TestTryLogin:
    """Tests for _try_login."""

    async def test_returns_cookies_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.cookies = {"n8n-auth": "session-cookie"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _try_login("http://localhost:45678", "a@b.com", "pass")

        assert result == {"n8n-auth": "session-cookie"}

    async def test_returns_none_on_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _try_login("http://localhost:45678", "a@b.com", "wrong")

        assert result is None


@pytest.mark.asyncio
class TestCreateOwner:
    """Tests for _create_owner."""

    async def test_returns_cookies_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.cookies = {"n8n-auth": "owner-cookie"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _create_owner("http://localhost:45678", "a@b.com", "pass")

        assert result == {"n8n-auth": "owner-cookie"}

    async def test_returns_none_when_owner_exists(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"message":"Instance owner already setup"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _create_owner("http://localhost:45678", "a@b.com", "pass")

        assert result is None


@pytest.mark.asyncio
class TestCreateApiKey:
    """Tests for _create_api_key."""

    async def test_extracts_key_from_data_wrapper(self):
        """Handles n8n response with {data: {rawApiKey: ...}} wrapper."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"rawApiKey": "test-key-123", "label": "laya-engine"}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _create_api_key("http://localhost:45678", {"n8n-auth": "cookie"})

        assert result == "test-key-123"

    async def test_extracts_key_from_flat_response(self):
        """Handles n8n response with {rawApiKey: ...} directly."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rawApiKey": "flat-key-456"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
            result = await _create_api_key("http://localhost:45678", {"n8n-auth": "cookie"})

        assert result == "flat-key-456"


@pytest.mark.asyncio
class TestEnsureN8nReady:
    """Tests for the main ensure_n8n_ready orchestrator."""

    async def test_returns_unreachable_when_n8n_down(self):
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=False):
            with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                result = await ensure_n8n_ready()

        assert result["status"] == "unreachable"
        assert "not running" in result["message"]

    async def test_returns_already_configured_when_key_valid(self):
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=True):
            with patch("laya.integrations.n8n_bootstrap._test_existing_api_key", new_callable=AsyncMock, return_value=True):
                with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                    result = await ensure_n8n_ready()

        assert result["status"] == "already_configured"
        assert result["has_api_key"] is True

    async def test_full_bootstrap_fresh_n8n(self):
        """Fresh n8n: create owner succeeds → create API key → import workflows."""
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=True):
            with patch("laya.integrations.n8n_bootstrap._test_existing_api_key", new_callable=AsyncMock, return_value=False):
                with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value=None):
                    with patch("laya.integrations.n8n_bootstrap._create_owner", new_callable=AsyncMock, return_value={"n8n-auth": "cookie"}):
                        with patch("laya.integrations.n8n_bootstrap.store_api_key", return_value=True):
                            with patch("laya.integrations.n8n_bootstrap._create_api_key", new_callable=AsyncMock, return_value="test-api-key"):
                                with patch("laya.integrations.n8n_bootstrap.import_workflows", new_callable=AsyncMock, return_value=5):
                                    with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                                        result = await ensure_n8n_ready()

        assert result["status"] == "ready"
        assert result["has_api_key"] is True
        assert "5 workflows" in result["message"]

    async def test_login_when_owner_exists(self):
        """Owner already exists (create returns None) → login succeeds → create API key."""
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=True):
            with patch("laya.integrations.n8n_bootstrap._test_existing_api_key", new_callable=AsyncMock, return_value=False):
                with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value="stored_pass"):
                    with patch("laya.integrations.n8n_bootstrap._create_owner", new_callable=AsyncMock, return_value=None):
                        with patch("laya.integrations.n8n_bootstrap._try_login", new_callable=AsyncMock, return_value={"n8n-auth": "cookie"}):
                            with patch("laya.integrations.n8n_bootstrap._create_api_key", new_callable=AsyncMock, return_value="new-key"):
                                with patch("laya.integrations.n8n_bootstrap.store_api_key", return_value=True):
                                    with patch("laya.integrations.n8n_bootstrap.import_workflows", new_callable=AsyncMock, return_value=0):
                                        with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                                            result = await ensure_n8n_ready()

        assert result["status"] == "ready"

    async def test_returns_error_when_login_fails(self):
        """Owner exists but login fails → error."""
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=True):
            with patch("laya.integrations.n8n_bootstrap._test_existing_api_key", new_callable=AsyncMock, return_value=False):
                with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value="wrong_pass"):
                    with patch("laya.integrations.n8n_bootstrap._create_owner", new_callable=AsyncMock, return_value=None):
                        with patch("laya.integrations.n8n_bootstrap._try_login", new_callable=AsyncMock, return_value=None):
                            with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                                result = await ensure_n8n_ready()

        assert result["status"] == "error"
        assert "cannot authenticate" in result["message"].lower()

    async def test_returns_error_when_api_key_creation_fails(self):
        """Authenticated but API key creation fails → error."""
        with patch("laya.integrations.n8n_bootstrap._wait_for_n8n", new_callable=AsyncMock, return_value=True):
            with patch("laya.integrations.n8n_bootstrap._test_existing_api_key", new_callable=AsyncMock, return_value=False):
                with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value=None):
                    with patch("laya.integrations.n8n_bootstrap._create_owner", new_callable=AsyncMock, return_value={"n8n-auth": "cookie"}):
                        with patch("laya.integrations.n8n_bootstrap.store_api_key", return_value=True):
                            with patch("laya.integrations.n8n_bootstrap._create_api_key", new_callable=AsyncMock, return_value=None):
                                with patch("laya.integrations.n8n_bootstrap.get_n8n_config", return_value={"base_url": "http://localhost:45678"}):
                                    result = await ensure_n8n_ready()

        assert result["status"] == "error"
        assert "failed to create api key" in result["message"].lower()


@pytest.mark.asyncio
class TestImportWorkflows:
    """Tests for workflow import."""

    async def test_imports_workflow_files(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "test-workflow.json").write_text(json.dumps({"name": "Test", "active": True}))

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("laya.integrations.n8n_bootstrap.WORKFLOWS_DIR", wf_dir):
            with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value="test-key"):
                with patch("laya.integrations.n8n_bootstrap.httpx.AsyncClient", return_value=mock_client):
                    count = await import_workflows("http://localhost:45678")

        assert count == 1

    async def test_skips_when_no_api_key(self):
        with patch("laya.integrations.n8n_bootstrap.get_api_key", return_value=None):
            count = await import_workflows("http://localhost:45678")

        assert count == 0


@pytest.mark.asyncio
class TestBootstrapEndpoint:
    """Tests for POST /settings/n8n/bootstrap endpoint."""

    async def test_endpoint_returns_result(self):
        from laya.main import app

        mock_result = {
            "status": "ready",
            "message": "n8n provisioned successfully (5 workflows imported)",
            "has_api_key": True,
        }

        with patch("laya.api.settings_api.ensure_n8n_ready", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/settings/n8n/bootstrap")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["has_api_key"] is True
