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
        assert len(config["webhooks"]) == 7

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
    """Test that executor reads webhook config dynamically."""

    async def test_executor_uses_configured_webhook(self, db):
        """Executor builds webhook URL from get_n8n_config()."""
        from laya.pipeline.executor import execute_action
        from tests.conftest import insert_test_event

        # Seed event + card
        await insert_test_event(db, event_id="evt_cfg")
        actions = json.dumps([{
            "action_id": "act_cfg",
            "label": "Comment",
            "action_type": "comment",
            "target_platform": "jira",
            "payload": {"body": "test"},
        }])
        await db.execute(
            "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
            "header, summary, suggested_actions, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("card_cfg", "evt_cfg", "HIGH", "ENGINEER", "CODE", "Test", "Test",
             actions, "pending"),
        )
        await db.commit()

        posted_url = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "result": {}}
        mock_resp.status_code = 200

        async def capture_post(url, json=None, timeout=None):
            nonlocal posted_url
            posted_url = url
            return mock_resp

        mock_client = MagicMock()
        mock_client.post = capture_post

        custom_config = {
            "base_url": "http://my-n8n:9999",
            "webhooks": {"jira": "my-custom-jira"},
        }

        with patch("laya.pipeline.executor.get_n8n_config", return_value=custom_config):
            with patch("laya.pipeline.executor.get_client", return_value=mock_client):
                with patch("laya.pipeline.executor.manager", MagicMock(broadcast=AsyncMock())):
                    await execute_action("card_cfg", "act_cfg")

        assert posted_url == "http://my-n8n:9999/webhook/my-custom-jira"

    async def test_executor_fallback_for_unknown_platform(self, db):
        """Unknown platform falls back to {platform}-executor."""
        from laya.pipeline.executor import execute_action
        from tests.conftest import insert_test_event

        await insert_test_event(db, event_id="evt_unk", platform="github",
                                raw_event_type="pr_opened",
                                subject_type="pr", subject_id="PR-1",
                                subject_title="Test")
        actions = json.dumps([{
            "action_id": "act_unk",
            "label": "Merge",
            "action_type": "merge",
            "target_platform": "github",
            "payload": {},
        }])
        await db.execute(
            "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
            "header, summary, suggested_actions, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("card_unk", "evt_unk", "HIGH", "ENGINEER", "CODE", "Test", "Test",
             actions, "pending"),
        )
        await db.commit()

        posted_url = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "result": {}}
        mock_resp.status_code = 200

        async def capture_post(url, json=None, timeout=None):
            nonlocal posted_url
            posted_url = url
            return mock_resp

        mock_client = MagicMock()
        mock_client.post = capture_post

        # Config with no github webhook defined
        config = {
            "base_url": "http://localhost:45678",
            "webhooks": {"jira": "jira-executor"},
        }

        with patch("laya.pipeline.executor.get_n8n_config", return_value=config):
            with patch("laya.pipeline.executor.get_client", return_value=mock_client):
                with patch("laya.pipeline.executor.manager", MagicMock(broadcast=AsyncMock())):
                    await execute_action("card_unk", "act_unk")

        assert posted_url == "http://localhost:45678/webhook/github-executor"
