# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the n8n egress backend — payload building and webhook resolution."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from laya.egress.backends.n8n import N8nBackend
from laya.egress.models import EgressRequest
from laya.egress.platforms import github, gmail, slack


@pytest.fixture
def backend():
    return N8nBackend()


class TestNormalizePayload:
    """Normalization is now owned by per-platform helper modules; n8n backend
    delegates.  These tests exercise the helpers directly."""

    def test_github_comment_field_normalization(self):
        payload = {"owner": "a", "repo": "b", "issue_number": "42", "body": "Hello"}
        result = github.normalize_payload("comment", payload)
        assert result["comment"] == "Hello"
        assert "body" not in result

    def test_github_issue_number_coercion(self):
        payload = {"owner": "a", "repo": "b", "issue_number": "42", "comment": "x"}
        result = github.normalize_payload("comment", payload)
        assert result["issue_number"] == 42

    def test_github_pr_number_coercion(self):
        payload = {"owner": "a", "repo": "b", "pr_number": "10"}
        result = github.normalize_payload("approve_pr", payload)
        assert result["pr_number"] == 10

    def test_gmail_body_normalization(self):
        payload = {"to": "a@b.com", "message": "Hello"}
        result = gmail.normalize_payload("send_email", payload)
        assert result["body"] == "Hello"
        assert "message" not in result

    def test_gmail_body_preserved(self):
        payload = {"to": "a@b.com", "body": "Existing"}
        result = gmail.normalize_payload("send_email", payload)
        assert result["body"] == "Existing"

    def test_slack_message_normalization(self):
        payload = {"channel": "general", "body": "Hello"}
        result = slack.normalize_payload("send_message", payload)
        assert result["message"] == "Hello"


class TestBuildPayload:
    @pytest.mark.asyncio
    async def test_payload_structure(self, backend):
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
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
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "sarah@co.com",
                "actor_name": "Sarah",
                "subject_id": "X-1",
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

    @pytest.mark.asyncio
    async def test_github_identifiers_derived_when_llm_omits(self, backend):
        """LLM payload missing owner/repo/issue_number — engine derives from event."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "a@b.com",
                "actor_name": "A",
                "subject_id": "10",
                "subject_title": "Issue 10",
                "source_platform": "github",
                "content_metadata": '{"repo": "aayushch/laya", "is_pr": false}',
            }])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="github",
                action_type="comment",
                payload={"comment": "Looking into this"},
                source_event_id="evt_github_issue_aayushch_laya_10_1776575508000",
            )
            payload = await backend._build_payload(request)

            assert payload["payload"]["owner"] == "aayushch"
            assert payload["payload"]["repo"] == "laya"
            assert payload["payload"]["issue_number"] == 10
            assert payload["payload"]["comment"] == "Looking into this"

    @pytest.mark.asyncio
    async def test_engine_wins_precedence_over_llm(self, backend):
        """Derived identifiers overwrite LLM-emitted values when both exist."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "a@b.com",
                "actor_name": "A",
                "subject_id": "10",
                "subject_title": "t",
                "source_platform": "github",
                "content_metadata": '{"repo": "real-owner/real-repo", "is_pr": false}',
            }])
            mock_get_db.return_value = mock_db

            # LLM hallucinates wrong owner/repo; event-derived values must win.
            request = EgressRequest(
                platform="github",
                action_type="comment",
                payload={"owner": "wrong", "repo": "wrong", "comment": "x"},
                source_event_id="evt_github_issue_real-owner_real-repo_10_1776",
            )
            payload = await backend._build_payload(request)

            assert payload["payload"]["owner"] == "real-owner"
            assert payload["payload"]["repo"] == "real-repo"

    @pytest.mark.asyncio
    async def test_jira_issue_key_from_subject_id(self, backend):
        """Jira derives issue_key from event_row.subject_id."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "a@b.com",
                "actor_name": "A",
                "subject_id": "PROJ-123",
                "subject_title": "Bug",
                "source_platform": "jira",
                "content_metadata": '{"jira_project": "PROJ"}',
            }])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="jira",
                action_type="comment",
                payload={"comment": "Fixed"},
                source_event_id="evt_jira_10017_1776",
            )
            payload = await backend._build_payload(request)

            assert payload["payload"]["issue_key"] == "PROJ-123"
            assert payload["payload"]["comment"] == "Fixed"

    @pytest.mark.asyncio
    async def test_slack_channel_from_metadata(self, backend):
        """Slack derives channel from content_metadata.slack_channel."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "",
                "actor_name": "Ally",
                "subject_id": "thread-C12345",
                "subject_title": "Message",
                "source_platform": "slack",
                "content_metadata": '{"slack_channel": "C12345", "slack_thread_ts": "1776.0001"}',
            }])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="slack",
                action_type="reply_thread",
                payload={"message": "Ack"},
                source_event_id="evt_slack_1776.0001",
            )
            payload = await backend._build_payload(request)

            assert payload["payload"]["channel"] == "C12345"
            assert payload["payload"]["thread_ts"] == "1776.0001"
            assert payload["payload"]["message"] == "Ack"

    @pytest.mark.asyncio
    async def test_gmail_reply_derives_threading_headers(self, backend):
        """Gmail reply must carry thread_id + in_reply_to + references + Re:-prefixed
        subject derived from the original event — Gmail's threading API requires all
        of them to link a reply into the original conversation."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[{
                "actor_email": "sender@example.com",
                "actor_name": "Sender",
                "subject_id": "thread-abc",
                "subject_title": "Weekly sync notes",
                "source_platform": "gmail",
                "content_metadata": (
                    '{"gmail_thread_id": "thread-abc", '
                    '"gmail_message_id_header": "<msg-1@mail.gmail.com>", '
                    '"gmail_references_header": "<root@x>"}'
                ),
            }])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.enrichment._get_self_emails", return_value=set()):
                request = EgressRequest(
                    platform="gmail",
                    action_type="send_email",
                    # LLM emits a deliberately drifted subject — engine must override it.
                    payload={"subject": "Quick response", "body": "Thanks!"},
                    source_event_id="evt_gmail_m1",
                )
                payload = await backend._build_payload(request)

            p = payload["payload"]
            # _build_payload runs build_api_payload for Gmail send_email,
            # which encodes headers into a raw MIME message + threadId.
            assert p["threadId"] == "thread-abc"
            assert "raw" in p

            # Decode the MIME message and verify threading headers.
            raw_padded = p["raw"] + "=" * (-len(p["raw"]) % 4)
            mime = base64.urlsafe_b64decode(raw_padded).decode("utf-8")
            assert "In-Reply-To: <msg-1@mail.gmail.com>" in mime
            assert "References: <root@x> <msg-1@mail.gmail.com>" in mime
            assert "Subject: Re: Weekly sync notes" in mime
            assert "To: sender@example.com" in mime
            assert mime.endswith("Thanks!")

    @pytest.mark.asyncio
    async def test_no_event_passes_payload_through(self, backend):
        """Direct egress (no source_event_id) — LLM/caller payload is authoritative."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            request = EgressRequest(
                platform="github",
                action_type="comment",
                payload={"owner": "o", "repo": "r", "issue_number": 5, "comment": "hi"},
                source_event_id=None,
            )
            payload = await backend._build_payload(request)

            # Caller-supplied identifiers untouched when no event present.
            assert payload["payload"]["owner"] == "o"
            assert payload["payload"]["repo"] == "r"
            assert payload["payload"]["issue_number"] == 5


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

    @pytest.mark.asyncio
    async def test_connection_id_routes_to_matching_executor(self, backend):
        """A requested connection_id routes to that connection's executor,
        not the first/arbitrary one."""
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[
                {"webhook_path": "cal-exec-personal", "space_id": "default", "workflow_id": "wf1", "connection_id": "conn_personal"},
                {"webhook_path": "cal-exec-laya", "space_id": "default", "workflow_id": "wf2", "connection_id": "conn_laya"},
            ])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {"calendar": "calendar-executor"},
            }):
                url = await backend._resolve_webhook_url("calendar", None, "conn_laya")
                assert "cal-exec-laya" in url
                assert "cal-exec-personal" not in url

    @pytest.mark.asyncio
    async def test_stale_connection_id_raises_not_misroutes(self, backend):
        """A requested connection_id with no matching executor (e.g. the account
        was disconnected/recreated and the UI sent a stale id) must raise rather
        than silently fall back to a DIFFERENT account's executor. Regression
        test for the wrong-account calendar bug."""
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[
                {"webhook_path": "cal-exec-personal", "space_id": "default", "workflow_id": "wf1", "connection_id": "conn_personal"},
                {"webhook_path": "cal-exec-laya", "space_id": "default", "workflow_id": "wf2", "connection_id": "conn_laya"},
            ])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {"calendar": "calendar-executor"},
            }):
                with pytest.raises(ValueError, match="no longer available"):
                    await backend._resolve_webhook_url("calendar", None, "conn_removed")

    @pytest.mark.asyncio
    async def test_unmatched_connection_with_no_executors_falls_back(self, backend):
        """When a connection_id is supplied but the platform has NO executor
        sources at all (e.g. the card path's "google_calendar" platform string,
        whose clones are stored under "calendar"), preserve the global-config
        fallback rather than raising — the guard targets only the
        wrong-account case where other executors exist."""
        with patch("laya.egress.backends.n8n.get_db") as mock_get_db:
            mock_db = AsyncMock()
            mock_db.execute_fetchall = AsyncMock(return_value=[])
            mock_get_db.return_value = mock_db

            with patch("laya.egress.backends.n8n.get_n8n_config", return_value={
                "base_url": "http://localhost:45678",
                "webhooks": {"google_calendar": "google-calendar-executor"},
            }):
                url = await backend._resolve_webhook_url("google_calendar", None, "conn_laya")
                assert url == "http://localhost:45678/webhook/google-calendar-executor"


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
    async def test_stale_connection_error_surfaced(self, backend):
        """execute() converts the resolver's ValueError (stale/removed account)
        into a failed EgressResult instead of dispatching to another account."""
        with patch.object(
            backend, "_resolve_webhook_url", new_callable=AsyncMock,
            side_effect=ValueError("The selected account is no longer available."),
        ):
            request = EgressRequest(
                platform="calendar", action_type="create_event",
                payload={}, connection_id="conn_removed",
            )
            result = await backend.execute(request, {})

            assert result.success is False
            assert "no longer available" in result.error

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
