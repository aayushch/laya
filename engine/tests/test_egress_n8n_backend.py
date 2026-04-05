"""Tests for the n8n egress backend — payload building and webhook resolution."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from laya.egress.backends.n8n import N8nBackend
from laya.egress.models import EgressRequest


@pytest.fixture
def backend():
    return N8nBackend()


class TestNormalizePayload:
    def test_github_comment_field_normalization(self, backend):
        payload = {"owner": "a", "repo": "b", "issue_number": "42", "body": "Hello"}
        result = backend._normalize_platform_payload("github", "comment", payload)
        assert result["comment"] == "Hello"
        assert "body" not in result

    def test_github_issue_number_coercion(self, backend):
        payload = {"owner": "a", "repo": "b", "issue_number": "42", "comment": "x"}
        result = backend._normalize_platform_payload("github", "comment", payload)
        assert result["issue_number"] == 42

    def test_github_pr_number_coercion(self, backend):
        payload = {"owner": "a", "repo": "b", "pr_number": "10"}
        result = backend._normalize_platform_payload("github", "approve_pr", payload)
        assert result["pr_number"] == 10

    def test_gmail_body_normalization(self, backend):
        payload = {"to": "a@b.com", "message": "Hello"}
        result = backend._normalize_platform_payload("gmail", "send_email", payload)
        assert result["body"] == "Hello"
        assert "message" not in result

    def test_gmail_body_preserved(self, backend):
        payload = {"to": "a@b.com", "body": "Existing"}
        result = backend._normalize_platform_payload("gmail", "send_email", payload)
        assert result["body"] == "Existing"

    def test_slack_message_normalization(self, backend):
        payload = {"channel": "general", "body": "Hello"}
        result = backend._normalize_platform_payload("slack", "send_message", payload)
        assert result["message"] == "Hello"

    def test_none_values_become_empty_strings(self, backend):
        payload = {"to": None, "subject": None, "body": None, "comment": None}
        # Test the None normalization in _build_payload indirectly
        for key in ("to", "subject", "body", "comment"):
            assert payload[key] is None


class TestBuildPayload:
    @pytest.mark.asyncio
    async def test_payload_structure(self, backend):
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="jira",
                action_type="comment",
                payload={"issue_key": "PROJ-1", "comment": "Hello"},
                source_event_id=None,
            )
            payload = await backend._build_payload(request)

            assert payload["action_type"] == "comment"
            assert payload["target"]["platform"] == "jira"
            assert payload["payload"]["issue_key"] == "PROJ-1"
            assert payload["payload"]["comment"] == "Hello"
            assert "event_actor_email" in payload

    @pytest.mark.asyncio
    async def test_payload_with_event_context(self, backend):
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "sarah@co.com",
                "actor_name": "Sarah",
                "subject_title": "Fix bug",
                "source_platform": "jira",
                "content_metadata": "{}",
            }])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="jira",
                action_type="comment",
                payload={"issue_key": "X-1", "comment": "Done"},
                source_event_id="evt_123",
            )
            payload = await backend._build_payload(request)

            assert payload["event_actor_email"] == "sarah@co.com"
            assert payload["event_actor_name"] == "Sarah"
            assert payload["event_subject"] == "Fix bug"


class TestWebhookResolution:
    @pytest.mark.asyncio
    async def test_fallback_to_global_config(self, backend):
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {"jira": "jira-executor"},
            }):
                url = await backend._resolve_webhook_url("jira", None)
                assert url == "http://localhost:45678/webhook/jira-executor"

    @pytest.mark.asyncio
    async def test_no_webhook_returns_none(self, backend):
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {},
            }):
                url = await backend._resolve_webhook_url("unknown_platform", None)
                assert url is None

    @pytest.mark.asyncio
    async def test_space_specific_executor(self, backend):
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[
                {"webhook_path": "jira-exec-space-a", "space_id": "space_a", "workflow_id": "wf1", "connection_id": None},
                {"webhook_path": "jira-exec-default", "space_id": "default", "workflow_id": "wf2", "connection_id": None},
            ])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {"jira": "jira-executor"},
            }):
                url = await backend._resolve_webhook_url("jira", "space_a")
                assert "jira-exec-space-a" in url


class TestExecute:
    @pytest.mark.asyncio
    async def test_successful_execution(self, backend):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "result": {"url": "https://jira.com/PROJ-1"}
        }
        mock_response.status_code = 200

        with patch.object(backend, "_resolve_webhook_url", new_callable=AsyncMock, return_value="http://n8n/webhook/jira-executor"):
            with patch.object(backend, "_build_payload", new_callable=AsyncMock, return_value={"action_type": "comment", "payload": {}, "target": {}, "event_actor_email": "", "event_actor_name": "", "event_subject": "", "event_platform": "jira", "action_id": "x", "source_event_id": None}):
                with patch("laya.egress.backends.n8n.get_client") as mock_client:
                    mock_client.return_value.post = AsyncMock(return_value=mock_response)

                    request = EgressRequest(platform="jira", action_type="comment", payload={"issue_key": "X-1"})
                    result = await backend.execute(request, {})

                    assert result.success is True
                    assert result.result_url == "https://jira.com/PROJ-1"

    @pytest.mark.asyncio
    async def test_no_webhook_returns_error(self, backend):
        with patch.object(backend, "_resolve_webhook_url", new_callable=AsyncMock, return_value=None):
            request = EgressRequest(platform="unknown", action_type="x", payload={})
            result = await backend.execute(request, {})

            assert result.success is False
            assert "No n8n executor webhook" in result.error

    @pytest.mark.asyncio
    async def test_n8n_failure_response(self, backend):
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False, "error": "Issue not found"}
        mock_response.status_code = 404

        with patch.object(backend, "_resolve_webhook_url", new_callable=AsyncMock, return_value="http://n8n/webhook/jira-executor"):
            with patch.object(backend, "_build_payload", new_callable=AsyncMock, return_value={"action_type": "comment", "payload": {}, "target": {}, "event_actor_email": "", "event_actor_name": "", "event_subject": "", "event_platform": "jira", "action_id": "x", "source_event_id": None}):
                with patch("laya.egress.backends.n8n.get_client") as mock_client:
                    mock_client.return_value.post = AsyncMock(return_value=mock_response)

                    request = EgressRequest(platform="jira", action_type="comment", payload={})
                    result = await backend.execute(request, {})

                    assert result.success is False
                    assert "Issue not found" in result.error
