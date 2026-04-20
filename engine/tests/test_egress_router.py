"""Tests for egress router — preview rendering (registry-driven) and backend
resolution."""

from unittest.mock import AsyncMock, patch

import pytest

from laya.egress.models import EgressRequest
from laya.egress.registry import get_capability
from laya.egress.router import _build_warnings, _render_summary, build_preview


class TestRenderSummary:
    """Per-platform summary rendering sources its template from the registry.
    These tests exercise ``_render_summary`` directly so we can isolate the
    template/placeholder logic from event enrichment."""

    def test_gmail_send(self):
        cap = get_capability("gmail", "send_email")
        s = _render_summary(cap, "gmail", "send_email", {"to": "sarah@co.com", "subject": "Re: Nav"})
        assert "sarah@co.com" in s
        assert "Re: Nav" in s

    def test_gmail_archive_static_template(self):
        cap = get_capability("gmail", "archive")
        s = _render_summary(cap, "gmail", "archive", {})
        assert "archive" in s.lower()

    def test_jira_comment(self):
        cap = get_capability("jira", "comment")
        s = _render_summary(cap, "jira", "comment", {"issue_key": "PROJ-123", "comment": "Fixed it"})
        assert "PROJ-123" in s

    def test_jira_transition(self):
        cap = get_capability("jira", "transition")
        s = _render_summary(cap, "jira", "transition", {"issue_key": "PROJ-123", "target_status": "Done"})
        assert "PROJ-123" in s
        assert "Done" in s

    def test_jira_create_issue(self):
        cap = get_capability("jira", "create_issue")
        s = _render_summary(cap, "jira", "create_issue", {"project": "PROJ", "summary": "New bug"})
        assert "PROJ" in s
        assert "New bug" in s

    def test_github_gh_ref_placeholder_full(self):
        cap = get_capability("github", "comment")
        s = _render_summary(cap, "github", "comment", {"owner": "acme", "repo": "api", "issue_number": 42})
        assert "acme/api#42" in s

    def test_github_gh_ref_placeholder_pr_number(self):
        cap = get_capability("github", "approve_pr")
        s = _render_summary(cap, "github", "approve_pr", {"owner": "acme", "repo": "api", "pr_number": 10})
        assert "Approve" in s
        assert "acme/api#10" in s

    def test_github_merge_uses_template_fields(self):
        cap = get_capability("github", "merge_pr")
        s = _render_summary(cap, "github", "merge_pr", {"owner": "a", "repo": "b", "pr_number": 5})
        assert "Merge" in s
        assert "a/b#5" in s

    def test_bitbucket_bb_ref_placeholder(self):
        cap = get_capability("bitbucket", "approve_pr")
        s = _render_summary(cap, "bitbucket", "approve_pr", {"workspace": "ws", "repo": "r", "pr_id": "7"})
        assert "Approve" in s
        assert "ws/r PR #7" in s

    def test_bitbucket_merge_bb_ref(self):
        cap = get_capability("bitbucket", "merge_pr")
        s = _render_summary(cap, "bitbucket", "merge_pr", {"workspace": "ws", "repo": "r", "pr_id": "99"})
        assert "ws/r PR #99" in s

    def test_slack_send(self):
        cap = get_capability("slack", "send_message")
        s = _render_summary(cap, "slack", "send_message", {"channel": "general"})
        assert "general" in s

    def test_slack_reply(self):
        cap = get_capability("slack", "reply_thread")
        s = _render_summary(cap, "slack", "reply_thread", {"channel": "dev"})
        assert "dev" in s
        assert "thread" in s.lower()

    def test_unknown_action_falls_back(self):
        """No capability → label-less fallback using action_type."""
        s = _render_summary(None, "nope", "foo", {})
        assert "foo" in s
        assert "nope" in s

    def test_missing_identifier_renders_unknown(self):
        """{gh_ref} with no owner/repo/number → "unknown", not a mangled string."""
        cap = get_capability("github", "comment")
        s = _render_summary(cap, "github", "comment", {"comment": "x"})
        assert "unknown" in s

    def test_missing_payload_field_renders_unknown_placeholder(self):
        """Non-computed placeholders fall through the defaultdict."""
        cap = get_capability("jira", "comment")
        s = _render_summary(cap, "jira", "comment", {"comment": "x"})
        # issue_key missing from payload → "unknown" in rendered summary
        assert "unknown" in s


class TestBuildWarnings:
    def test_merge_pr_static_warning_from_registry(self):
        cap = get_capability("github", "merge_pr")
        warnings = _build_warnings(cap, "merge_pr", {})
        assert any("merge" in w.lower() for w in warnings)

    def test_bitbucket_decline_static_warning(self):
        cap = get_capability("bitbucket", "decline_pr")
        warnings = _build_warnings(cap, "decline_pr", {})
        assert any("decline" in w.lower() for w in warnings)

    def test_transition_to_terminal_dynamic_warning(self):
        cap = get_capability("jira", "transition")
        warnings = _build_warnings(cap, "transition", {"target_status": "Closed"})
        assert any("Closed" in w for w in warnings)

    def test_transition_to_nonterminal_no_dynamic_warning(self):
        cap = get_capability("jira", "transition")
        warnings = _build_warnings(cap, "transition", {"target_status": "In Progress"})
        assert warnings == []

    def test_comment_has_no_warnings(self):
        cap = get_capability("jira", "comment")
        warnings = _build_warnings(cap, "comment", {"comment": "Hello"})
        assert warnings == []

    def test_many_recipients_dynamic_warning(self):
        cap = get_capability("gmail", "send_email")
        warnings = _build_warnings(
            cap, "send_email",
            {"to": "a@b.com", "cc": "c@d.com,e@f.com,g@h.com"},
        )
        assert any("recipients" in w.lower() for w in warnings)

    def test_few_recipients_no_warning(self):
        cap = get_capability("gmail", "send_email")
        warnings = _build_warnings(
            cap, "send_email",
            {"to": "a@b.com", "cc": "c@d.com"},
        )
        # 2 recipients total — below the 3-recipient threshold
        assert not any("recipients" in w.lower() for w in warnings)

    def test_no_capability_yields_empty_warnings_plus_dynamic(self):
        """Capability missing but action_type triggers a dynamic rule."""
        warnings = _build_warnings(None, "transition", {"target_status": "Done"})
        assert any("Done" in w for w in warnings)


def _no_event_db():
    """Return a mocked get_db() whose query returns no event row."""
    mock_db = AsyncMock()
    mock_db.execute_fetchall = AsyncMock(return_value=[])
    return mock_db


def _event_db(row: dict):
    """Return a mocked get_db() whose query returns a single event row."""
    mock_db = AsyncMock()
    mock_db.execute_fetchall = AsyncMock(return_value=[row])
    return mock_db


@pytest.mark.asyncio
class TestBuildPreview:
    """``build_preview`` runs the same event-based enrichment as execute,
    so identifiers derived from the source event flow into the summary."""

    async def test_preview_structure(self):
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_get_db.return_value = _no_event_db()
            req = EgressRequest(
                platform="jira",
                action_type="comment",
                payload={"issue_key": "X-1", "comment": "Hi"},
            )
            preview = await build_preview(req)
            assert preview.platform == "jira"
            assert preview.action_type == "comment"
            assert preview.summary
            assert isinstance(preview.warnings, list)
            assert preview.estimated_impact in ("low", "medium", "high")

    async def test_merge_is_high_impact(self):
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_get_db.return_value = _no_event_db()
            req = EgressRequest(
                platform="github", action_type="merge_pr",
                payload={"owner": "a", "repo": "b", "pr_number": 1},
            )
            preview = await build_preview(req)
            assert preview.estimated_impact == "high"

    async def test_comment_is_medium_impact(self):
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_get_db.return_value = _no_event_db()
            req = EgressRequest(
                platform="jira", action_type="comment",
                payload={"issue_key": "X-1", "comment": "note"},
            )
            preview = await build_preview(req)
            assert preview.estimated_impact == "medium"

    async def test_preview_enriches_identifiers_from_event(self):
        """Closes the silent regression: when the caller omits identifiers
        (as the stager does post-refactor), preview fetches the event and
        derives them so the summary isn't "Post comment on unknown"."""
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_get_db.return_value = _event_db({
                "actor_email": "a@b.com",
                "actor_name": "A",
                "subject_id": "PROJ-999",
                "subject_title": "Bug",
                "source_platform": "jira",
                "content_metadata": '{"jira_project": "PROJ"}',
            })
            req = EgressRequest(
                platform="jira",
                action_type="comment",
                payload={"comment": "Fixed"},  # no issue_key from caller
                source_event_id="evt_jira_10017_1776",
            )
            preview = await build_preview(req)
            assert "PROJ-999" in preview.summary
            assert preview.details["issue_key"] == "PROJ-999"

    async def test_preview_github_identifiers_from_event(self):
        with patch("laya.egress.enrichment.get_db") as mock_get_db:
            mock_get_db.return_value = _event_db({
                "actor_email": "a@b.com",
                "actor_name": "A",
                "subject_id": "10",
                "subject_title": "Issue 10",
                "source_platform": "github",
                "content_metadata": '{"repo": "acme/api", "is_pr": false}',
            })
            req = EgressRequest(
                platform="github",
                action_type="comment",
                payload={"comment": "Looking"},
                source_event_id="evt_github_issue_acme_api_10_1776",
            )
            preview = await build_preview(req)
            assert "acme/api#10" in preview.summary
