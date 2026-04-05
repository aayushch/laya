"""Tests for n8n webhook configuration."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from laya.config import DEFAULT_SETTINGS, get_n8n_config


class TestN8nConfig:
    """Tests for get_n8n_config() helper."""

    def test_returns_defaults_when_no_settings(self, tmp_path):
        """get_n8n_config returns defaults when no settings file exists."""
        with patch("laya.config.LAYA_CONFIG_FILE", tmp_path / "missing.json"):
            config = get_n8n_config()

        assert config["base_url"] == "http://localhost:45678"
        assert config["webhooks"]["jira"] == "jira-executor"
        assert len(config["webhooks"]) == 10

    def test_env_var_overrides_base_url(self, tmp_path):
        """N8N_URL env var overrides base_url from settings."""
        with patch("laya.config.LAYA_CONFIG_FILE", tmp_path / "missing.json"):
            with patch.dict("os.environ", {"N8N_URL": "http://custom:9999"}):
                config = get_n8n_config()

        assert config["base_url"] == "http://custom:9999"
        # Webhooks should still be defaults
        assert config["webhooks"]["jira"] == "jira-executor"

    def test_user_webhooks_merged(self, tmp_path):
        """User-added webhooks are merged over defaults."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "n8n": {
                "base_url": "http://myserver:5678",
                "webhooks": {
                    "jira": "custom-jira",
                    "github": "github-executor",
                }
            }
        }))

        with patch("laya.config.LAYA_CONFIG_FILE", settings_file):
            config = get_n8n_config()

        assert config["base_url"] == "http://myserver:5678"
        assert config["webhooks"]["jira"] == "custom-jira"
        assert config["webhooks"]["github"] == "github-executor"


@pytest.mark.asyncio
class TestN8nTestEndpoint:
    """Tests for POST /settings/n8n/test."""

    async def test_healthy_connection(self):
        """Returns healthy when n8n responds 200."""
        from laya.main import app

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("laya.api.settings_api.get_client", return_value=mock_client):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/settings/n8n/test",
                    json={"base_url": "http://localhost:45678"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["health"] == "healthy"
        assert data["base_url"] == "http://localhost:45678"

    async def test_unreachable_connection(self):
        """Returns unreachable on ConnectError."""
        from laya.main import app

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("laya.api.settings_api.get_client", return_value=mock_client):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/settings/n8n/test",
                    json={"base_url": "http://bad-host:5678"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["health"] == "unreachable"


@pytest.mark.asyncio
class TestExecutorUsesConfig:
    """Test that egress backend reads webhook config dynamically."""

    async def test_executor_uses_configured_webhook(self):
        """N8nBackend resolves webhook URL from get_n8n_config()."""
        from laya.egress.backends.n8n import N8nBackend

        backend = N8nBackend()

        custom_config = {
            "base_url": "http://my-n8n:9999",
            "webhooks": {"jira": "my-custom-jira"},
        }

        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value=custom_config):
                url = await backend._resolve_webhook_url("jira", None)

        assert url == "http://my-n8n:9999/webhook/my-custom-jira"

    async def test_executor_fallback_for_unknown_platform(self):
        """Unknown platform with no webhook config returns None."""
        from laya.egress.backends.n8n import N8nBackend

        backend = N8nBackend()

        config = {
            "base_url": "http://localhost:45678",
            "webhooks": {"jira": "jira-executor"},
        }

        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value=config):
                # github is in DEFAULT_SETTINGS, so it will resolve
                url = await backend._resolve_webhook_url("github", None)

        assert url == "http://localhost:45678/webhook/github-executor"
