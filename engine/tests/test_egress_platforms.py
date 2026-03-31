"""Tests for platform-specific payload normalization and validation."""

import pytest

from laya.egress.platforms import gmail, jira, github, bitbucket, slack, outlook, linear, calendar


class TestGmail:
    def test_normalize_body_from_message(self):
        p = gmail.normalize_payload("send_email", {"to": "a@b.com", "message": "Hello"})
        assert p["body"] == "Hello"
        assert "message" not in p

    def test_normalize_body_from_content(self):
        p = gmail.normalize_payload("send_email", {"to": "a@b.com", "content": "Hi"})
        assert p["body"] == "Hi"

    def test_body_preserved_if_present(self):
        p = gmail.normalize_payload("send_email", {"to": "a@b.com", "body": "Existing"})
        assert p["body"] == "Existing"

    def test_reply_adds_re_prefix(self):
        p = gmail.normalize_payload("send_email", {"subject": "Hello", "thread_id": "t123", "body": "x"})
        assert p["subject"] == "Re: Hello"

    def test_reply_no_double_re(self):
        p = gmail.normalize_payload("send_email", {"subject": "Re: Hello", "thread_id": "t123", "body": "x"})
        assert p["subject"] == "Re: Hello"

    def test_forward_adds_fwd_prefix(self):
        p = gmail.normalize_payload("forward", {"subject": "Hello", "body": "x"})
        assert p["subject"] == "Fwd: Hello"

    def test_validate_send_missing_to(self):
        errors = gmail.validate_payload("send_email", {"body": "Hello"})
        assert any("to" in e.lower() for e in errors)

    def test_validate_send_ok(self):
        errors = gmail.validate_payload("send_email", {"to": "a@b.com", "body": "Hello"})
        assert errors == []

    def test_validate_archive_missing_id(self):
        errors = gmail.validate_payload("archive", {})
        assert any("gmail_id" in e for e in errors)


class TestJira:
    def test_normalize_issue_key_from_variants(self):
        p = jira.normalize_payload("comment", {"issueKey": "BUG-1", "comment": "Hi"})
        assert p["issue_key"] == "BUG-1"
        assert "issueKey" not in p

    def test_normalize_ticket_id(self):
        p = jira.normalize_payload("comment", {"ticket_id": "BUG-2", "comment": "x"})
        assert p["issue_key"] == "BUG-2"

    def test_normalize_comment_from_body(self):
        p = jira.normalize_payload("comment", {"issue_key": "X-1", "body": "My comment"})
        assert p["comment"] == "My comment"

    def test_normalize_create_issue_defaults(self):
        p = jira.normalize_payload("create_issue", {"project": "PROJ", "title": "Bug"})
        assert p["summary"] == "Bug"
        assert p["type"] == "Task"

    def test_validate_comment_ok(self):
        errors = jira.validate_payload("comment", {"issue_key": "X-1", "comment": "Hello"})
        assert errors == []

    def test_validate_comment_missing_key(self):
        errors = jira.validate_payload("comment", {"comment": "Hello"})
        assert any("issue_key" in e for e in errors)

    def test_validate_transition_missing_status(self):
        errors = jira.validate_payload("transition", {"issue_key": "X-1"})
        assert any("target_status" in e for e in errors)

    def test_validate_create_issue_missing_project(self):
        errors = jira.validate_payload("create_issue", {"summary": "Bug"})
        assert any("project" in e for e in errors)


class TestGitHub:
    def test_normalize_comment_from_body(self):
        p = github.normalize_payload("comment", {"owner": "a", "repo": "b", "issue_number": "5", "body": "Hi"})
        assert p["comment"] == "Hi"
        assert "body" not in p

    def test_normalize_issue_number_to_int(self):
        p = github.normalize_payload("comment", {"owner": "a", "repo": "b", "issue_number": "42", "comment": "x"})
        assert p["issue_number"] == 42
        assert isinstance(p["issue_number"], int)

    def test_normalize_pr_number_from_issue_number(self):
        p = github.normalize_payload("approve_pr", {"owner": "a", "repo": "b", "issue_number": 7})
        assert p["pr_number"] == 7

    def test_normalize_merge_method_default(self):
        p = github.normalize_payload("merge_pr", {"owner": "a", "repo": "b", "pr_number": 1})
        assert p["merge_method"] == "squash"

    def test_validate_comment_ok(self):
        errors = github.validate_payload("comment", {"owner": "a", "repo": "b", "issue_number": 1})
        assert errors == []

    def test_validate_missing_owner(self):
        errors = github.validate_payload("comment", {"repo": "b", "issue_number": 1})
        assert any("owner" in e for e in errors)

    def test_validate_approve_missing_pr(self):
        errors = github.validate_payload("approve_pr", {"owner": "a", "repo": "b"})
        assert any("pr_number" in e for e in errors)

    def test_validate_create_pr_missing_branches(self):
        errors = github.validate_payload("create_pr", {"owner": "a", "repo": "b", "title": "x"})
        assert any("head" in e for e in errors)
        assert any("base" in e for e in errors)


class TestBitbucket:
    def test_normalize_comment_from_body(self):
        p = bitbucket.normalize_payload("comment_pr", {"workspace": "w", "repo": "r", "pr_id": "1", "body": "Hi"})
        assert p["comment"] == "Hi"

    def test_normalize_pr_id_from_number(self):
        p = bitbucket.normalize_payload("approve_pr", {"workspace": "w", "repo": "r", "pr_number": 5})
        assert p["pr_id"] == "5"

    def test_normalize_merge_strategy_default(self):
        p = bitbucket.normalize_payload("merge_pr", {"workspace": "w", "repo": "r", "pr_id": "1"})
        assert p["merge_strategy"] == "squash"

    def test_validate_comment_ok(self):
        errors = bitbucket.validate_payload("comment_pr", {"workspace": "w", "repo": "r", "pr_id": "1", "comment": "x"})
        assert errors == []

    def test_validate_missing_workspace(self):
        errors = bitbucket.validate_payload("approve_pr", {"repo": "r", "pr_id": "1"})
        assert any("workspace" in e for e in errors)

    def test_validate_create_pr_missing_branches(self):
        errors = bitbucket.validate_payload("create_pr", {"workspace": "w", "repo": "r", "title": "x"})
        assert any("source_branch" in e for e in errors)


class TestSlack:
    def test_normalize_message_from_body(self):
        p = slack.normalize_payload("send_message", {"channel": "general", "body": "Hello"})
        assert p["message"] == "Hello"

    def test_strip_hash_from_channel(self):
        p = slack.normalize_payload("send_message", {"channel": "#general", "message": "Hi"})
        assert p["channel"] == "general"

    def test_validate_send_ok(self):
        errors = slack.validate_payload("send_message", {"channel": "general", "message": "Hi"})
        assert errors == []

    def test_validate_missing_channel(self):
        errors = slack.validate_payload("send_message", {"message": "Hi"})
        assert any("channel" in e for e in errors)

    def test_validate_reply_missing_thread(self):
        errors = slack.validate_payload("reply_thread", {"channel": "general", "message": "Hi"})
        assert any("thread_ts" in e for e in errors)

    def test_validate_react_missing_emoji(self):
        errors = slack.validate_payload("react", {"channel": "general", "timestamp": "123"})
        assert any("emoji" in e for e in errors)


class TestOutlook:
    def test_normalize_body_from_message(self):
        p = outlook.normalize_payload("send_email", {"to": "a@b.com", "message": "Hi"})
        assert p["body"] == "Hi"

    def test_reply_adds_re_prefix(self):
        p = outlook.normalize_payload("send_email", {"subject": "Hello", "conversation_id": "c1", "body": "x"})
        assert p["subject"] == "Re: Hello"


class TestLinear:
    def test_normalize_issue_id_from_ticket(self):
        p = linear.normalize_payload("comment", {"ticket_id": "LIN-123", "body": "Hi"})
        assert p["issue_id"] == "LIN-123"

    def test_normalize_team_id_from_project(self):
        p = linear.normalize_payload("create_issue", {"project": "TEAM", "title": "Bug"})
        assert p["team_id"] == "TEAM"

    def test_normalize_comment_body_from_comment(self):
        p = linear.normalize_payload("comment", {"issue_id": "X", "comment": "Hello"})
        assert p["body"] == "Hello"

    def test_validate_comment_ok(self):
        errors = linear.validate_payload("comment", {"issue_id": "X", "body": "Hello"})
        assert errors == []

    def test_validate_create_missing_team(self):
        errors = linear.validate_payload("create_issue", {"title": "Bug"})
        assert any("team_id" in e for e in errors)


class TestCalendar:
    def test_normalize_title_from_summary(self):
        p = calendar.normalize_payload("create_event", {"summary": "Meeting", "start": "t1", "end": "t2"})
        assert p["title"] == "Meeting"

    def test_normalize_datetime_fields(self):
        p = calendar.normalize_payload("create_event", {"title": "M", "start_time": "t1", "end_time": "t2"})
        assert p["start"] == "t1"
        assert p["end"] == "t2"

    def test_validate_create_event_ok(self):
        errors = calendar.validate_payload("create_event", {"title": "M", "start": "t1", "end": "t2"})
        assert errors == []

    def test_validate_create_missing_start(self):
        errors = calendar.validate_payload("create_event", {"title": "M", "end": "t2"})
        assert any("start" in e for e in errors)

    def test_validate_update_missing_event_id(self):
        errors = calendar.validate_payload("update_event", {})
        assert any("event_id" in e for e in errors)
