"""Tests for egress data models."""

import pytest

from laya.egress.models import (
    Connection,
    ConnectionResult,
    EgressCapability,
    EgressPreview,
    EgressRequest,
    EgressResult,
)


class TestEgressRequest:
    def test_minimal_request(self):
        req = EgressRequest(platform="jira", action_type="comment", payload={"issue_key": "PROJ-1"})
        assert req.platform == "jira"
        assert req.action_type == "comment"
        assert req.payload == {"issue_key": "PROJ-1"}
        assert req.source_card_id is None
        assert req.space_id is None
        assert req.dry_run is False

    def test_full_request(self):
        req = EgressRequest(
            platform="gmail",
            action_type="send_email",
            payload={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
            source_card_id="card_123",
            source_event_id="evt_456",
            space_id="default",
            dry_run=True,
        )
        assert req.source_card_id == "card_123"
        assert req.space_id == "default"
        assert req.dry_run is True


class TestEgressResult:
    def test_success_result(self):
        result = EgressResult(success=True, result_url="https://jira.com/PROJ-1", result_data={"id": "123"})
        assert result.success is True
        assert result.result_url == "https://jira.com/PROJ-1"
        assert result.error is None
        assert result.retryable is False

    def test_failure_result(self):
        result = EgressResult(success=False, error="Connection refused", retryable=True)
        assert result.success is False
        assert result.error == "Connection refused"
        assert result.retryable is True

    def test_default_values(self):
        result = EgressResult(success=True)
        assert result.result_url is None
        assert result.result_data == {}
        assert result.error is None


class TestEgressPreview:
    def test_preview(self):
        preview = EgressPreview(
            platform="slack",
            action_type="send_message",
            summary="Send message to #general",
            details={"channel": "#general", "message": "Hello"},
            warnings=["This is a public channel"],
            estimated_impact="medium",
        )
        assert preview.platform == "slack"
        assert preview.summary == "Send message to #general"
        assert len(preview.warnings) == 1

    def test_default_impact(self):
        preview = EgressPreview(platform="jira", action_type="comment", summary="Comment on PROJ-1")
        assert preview.estimated_impact == "low"
        assert preview.warnings == []


class TestEgressCapability:
    def test_capability(self):
        cap = EgressCapability(
            action_type="comment",
            label="Post Comment",
            requires_fields=["issue_key", "comment"],
            optional_fields=["visibility"],
            description="Add a comment to a Jira issue.",
            confirmation_required=True,
        )
        assert cap.action_type == "comment"
        assert "issue_key" in cap.requires_fields
        assert cap.confirmation_required is True

    def test_defaults(self):
        cap = EgressCapability(action_type="star", label="Star")
        assert cap.requires_fields == []
        assert cap.confirmation_required is True


class TestConnection:
    def test_connection(self):
        conn = Connection(
            connection_id="conn_abc",
            platform="github",
            name="GitHub Main",
            status="connected",
            capabilities=["comment", "close_issue", "approve_pr"],
        )
        assert conn.platform == "github"
        assert len(conn.capabilities) == 3
        assert conn.n8n_credential_id is None

    def test_error_connection(self):
        conn = Connection(
            connection_id="conn_def",
            platform="jira",
            name="Jira",
            status="error",
            error_message="Token expired",
        )
        assert conn.status == "error"
        assert conn.error_message == "Token expired"


class TestConnectionResult:
    def test_success(self):
        result = ConnectionResult(status="connected", connection_id="conn_123", capabilities=["comment"])
        assert result.status == "connected"
        assert result.connection_id == "conn_123"

    def test_failure(self):
        result = ConnectionResult(status="failed", error="Invalid token")
        assert result.status == "failed"
        assert result.connection_id is None
