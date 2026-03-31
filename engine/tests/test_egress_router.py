"""Tests for egress router — backend resolution and preview building."""

import pytest

from laya.egress.models import EgressRequest
from laya.egress.router import build_preview, _build_summary, _build_warnings


class TestBuildSummary:
    def test_gmail_send(self):
        summary = _build_summary("gmail", "send_email", {"to": "sarah@co.com", "subject": "Re: Nav"})
        assert "sarah@co.com" in summary
        assert "Re: Nav" in summary

    def test_gmail_archive(self):
        summary = _build_summary("gmail", "archive", {})
        assert "archive" in summary.lower() or "Archive" in summary

    def test_jira_comment(self):
        summary = _build_summary("jira", "comment", {"issue_key": "PROJ-123", "comment": "Fixed it"})
        assert "PROJ-123" in summary

    def test_jira_transition(self):
        summary = _build_summary("jira", "transition", {"issue_key": "PROJ-123", "target_status": "Done"})
        assert "PROJ-123" in summary
        assert "Done" in summary

    def test_jira_create_issue(self):
        summary = _build_summary("jira", "create_issue", {"project": "PROJ", "summary": "New bug", "type": "Bug"})
        assert "PROJ" in summary
        assert "New bug" in summary

    def test_github_comment(self):
        summary = _build_summary("github", "comment", {"owner": "acme", "repo": "api", "issue_number": 42})
        assert "acme/api#42" in summary

    def test_github_approve_pr(self):
        summary = _build_summary("github", "approve_pr", {"owner": "acme", "repo": "api", "pr_number": 10})
        assert "Approve" in summary
        assert "acme/api#10" in summary

    def test_github_merge_pr(self):
        summary = _build_summary("github", "merge_pr", {"owner": "a", "repo": "b", "pr_number": 5, "merge_method": "squash"})
        assert "Merge" in summary
        assert "squash" in summary

    def test_bitbucket_approve(self):
        summary = _build_summary("bitbucket", "approve_pr", {"workspace": "ws", "repo": "r", "pr_id": "7"})
        assert "Approve" in summary
        assert "PR #7" in summary

    def test_slack_send(self):
        summary = _build_summary("slack", "send_message", {"channel": "#general"})
        assert "#general" in summary or "general" in summary

    def test_slack_reply(self):
        summary = _build_summary("slack", "reply_thread", {"channel": "#dev"})
        assert "Reply" in summary or "reply" in summary

    def test_unknown_action(self):
        summary = _build_summary("unknown_platform", "unknown_action", {})
        assert "unknown_platform" in summary


class TestBuildWarnings:
    def test_merge_warning(self):
        warnings = _build_warnings("github", "merge_pr", {})
        assert any("merge" in w.lower() for w in warnings)

    def test_decline_warning(self):
        warnings = _build_warnings("bitbucket", "decline_pr", {})
        assert any("decline" in w.lower() for w in warnings)

    def test_delete_event_warning(self):
        warnings = _build_warnings("calendar", "delete_event", {})
        assert any("delete" in w.lower() for w in warnings)

    def test_transition_to_terminal(self):
        warnings = _build_warnings("jira", "transition", {"target_status": "Closed"})
        assert len(warnings) > 0
        assert any("Closed" in w for w in warnings)

    def test_no_warning_for_comment(self):
        warnings = _build_warnings("jira", "comment", {"comment": "Hello"})
        assert warnings == []

    def test_many_recipients_warning(self):
        warnings = _build_warnings("gmail", "send_email", {"to": "a@b.com", "cc": "c@d.com,e@f.com,g@h.com"})
        assert any("recipients" in w.lower() for w in warnings)


@pytest.mark.asyncio
class TestBuildPreview:
    async def test_preview_structure(self):
        req = EgressRequest(platform="jira", action_type="comment", payload={"issue_key": "X-1", "comment": "Hi"})
        preview = await build_preview(req)
        assert preview.platform == "jira"
        assert preview.action_type == "comment"
        assert preview.summary  # non-empty
        assert isinstance(preview.warnings, list)
        assert preview.estimated_impact in ("low", "medium", "high")

    async def test_merge_is_high_impact(self):
        req = EgressRequest(platform="github", action_type="merge_pr", payload={"owner": "a", "repo": "b", "pr_number": 1})
        preview = await build_preview(req)
        assert preview.estimated_impact == "high"

    async def test_comment_is_medium_impact(self):
        req = EgressRequest(platform="jira", action_type="comment", payload={"issue_key": "X-1", "comment": "note"})
        preview = await build_preview(req)
        assert preview.estimated_impact == "medium"
