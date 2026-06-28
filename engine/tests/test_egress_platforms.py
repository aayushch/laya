# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for platform-specific payload normalization and validation."""

import pytest

# Bind each platform's singleton adapter to the short name so the existing
# `gmail.normalize_payload(...)` call sites exercise the Platform methods.
from laya.egress.platforms.bitbucket import PLATFORM as bitbucket
from laya.egress.platforms.calendar import PLATFORM as calendar
from laya.egress.platforms.github import PLATFORM as github
from laya.egress.platforms.gmail import PLATFORM as gmail
from laya.egress.platforms.jira import PLATFORM as jira
from laya.egress.platforms.linear import PLATFORM as linear
from laya.egress.platforms.outlook import PLATFORM as outlook
from laya.egress.platforms.slack import PLATFORM as slack


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

    def test_identifiers_populate_reply_headers(self):
        meta = {
            "gmail_thread_id": "t1",
            "gmail_message_id_header": "<abc@mail.gmail.com>",
            "gmail_references_header": "<root@x> <prev@y>",
        }
        event = {"actor_email": "sender@example.com", "subject_title": "Weekly update"}
        ids = gmail.identifiers_from_event("send_email", "evt_gmail_m1", meta, event)
        assert ids["thread_id"] == "t1"
        assert ids["in_reply_to"] == "<abc@mail.gmail.com>"
        assert ids["references"] == "<root@x> <prev@y> <abc@mail.gmail.com>"
        assert ids["original_subject"] == "Weekly update"
        assert ids["to"] == "sender@example.com"

    def test_identifiers_references_without_existing_chain(self):
        meta = {
            "gmail_thread_id": "t1",
            "gmail_message_id_header": "<abc@mail.gmail.com>",
        }
        event = {"actor_email": "s@e.com", "subject_title": "Hi"}
        ids = gmail.identifiers_from_event("send_email", "evt_gmail_m1", meta, event)
        assert ids["references"] == "<abc@mail.gmail.com>"

    def test_identifiers_no_reply_headers_when_no_thread(self):
        meta = {"gmail_message_id_header": "<abc@mail.gmail.com>"}
        event = {"actor_email": "s@e.com", "subject_title": "Hi"}
        ids = gmail.identifiers_from_event("send_email", "evt_gmail_m1", meta, event)
        assert "in_reply_to" not in ids
        assert "references" not in ids
        assert "original_subject" not in ids

    def test_identifiers_skip_reply_headers_on_forward(self):
        meta = {
            "gmail_thread_id": "t1",
            "gmail_message_id_header": "<abc@mail.gmail.com>",
        }
        event = {"actor_email": "s@e.com", "subject_title": "Hi"}
        ids = gmail.identifiers_from_event("forward", "evt_gmail_m1", meta, event)
        assert "in_reply_to" not in ids
        assert "references" not in ids
        # forward also shouldn't auto-populate "to"
        assert "to" not in ids

    def test_normalize_overrides_llm_subject_with_original(self):
        p = gmail.normalize_payload(
            "send_email",
            {
                "subject": "Response to your inquiry",
                "thread_id": "t123",
                "body": "x",
                "original_subject": "Weekly sync notes",
            },
        )
        assert p["subject"] == "Re: Weekly sync notes"
        assert "original_subject" not in p

    def test_normalize_strips_re_prefix_from_original_before_prefixing(self):
        p = gmail.normalize_payload(
            "send_email",
            {
                "subject": "anything",
                "thread_id": "t1",
                "body": "x",
                "original_subject": "Re: Weekly sync",
            },
        )
        assert p["subject"] == "Re: Weekly sync"

    def test_normalize_falls_back_to_llm_subject_without_original(self):
        p = gmail.normalize_payload(
            "send_email",
            {"subject": "Hello", "thread_id": "t1", "body": "x"},
        )
        assert p["subject"] == "Re: Hello"


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


class TestIdentifiersFromEvent:
    """Event-derived identifier extraction per platform."""

    def test_github_from_metadata(self):
        ids = github.identifiers_from_event(
            "comment",
            "evt_github_issue_aayushch_laya_10_1776",
            {"repo": "aayushch/laya", "is_pr": False},
            {},
        )
        assert ids == {"owner": "aayushch", "repo": "laya", "issue_number": 10}

    def test_github_pr_routes_number(self):
        ids = github.identifiers_from_event(
            "approve_pr",
            "evt_github_pr_acme_widgets_42_1776",
            {"repo": "acme/widgets", "is_pr": True},
            {},
        )
        assert ids["pr_number"] == 42
        assert "issue_number" not in ids

    def test_github_event_id_fallback_without_metadata(self):
        ids = github.identifiers_from_event(
            "comment", "evt_github_issue_a_b_7_1776", {}, {}
        )
        # event_id regex is ambiguous with underscores; single-token works
        assert ids.get("issue_number") == 7

    def test_github_comment_event_uses_metadata_number(self):
        # comment-kind event_id encodes comment_id, not issue_number;
        # issue_number must come from metadata.
        ids = github.identifiers_from_event(
            "comment",
            "evt_github_comment_o_r_9999_1776",
            {"repo": "o/r", "issue_number": 5},
            {},
        )
        assert ids == {"owner": "o", "repo": "r", "issue_number": 5}

    def test_github_no_source_returns_minimal(self):
        ids = github.identifiers_from_event("comment", None, {}, {})
        assert ids == {}

    def test_jira_issue_key_from_subject_id(self):
        ids = jira.identifiers_from_event(
            "comment",
            "evt_jira_10017_1776",
            {"jira_project": "PROJ"},
            {"subject_id": "PROJ-123"},
        )
        assert ids == {"issue_key": "PROJ-123", "project": "PROJ"}

    def test_jira_no_subject_returns_project_only(self):
        ids = jira.identifiers_from_event("create_issue", None, {"jira_project": "X"}, {})
        assert ids == {"project": "X"}

    def test_linear_issue_id_from_event_id(self):
        ids = linear.identifiers_from_event(
            "comment",
            "evt_linear_abcd-uuid-1234_1776",
            {"linear_team_id": "team-uuid"},
            {},
        )
        assert ids == {"issue_id": "abcd-uuid-1234", "team_id": "team-uuid"}

    def test_linear_empty_when_no_sources(self):
        ids = linear.identifiers_from_event("comment", None, {}, {})
        assert ids == {}

    def test_bitbucket_workspace_repo_and_prid(self):
        ids = bitbucket.identifiers_from_event(
            "comment_pr",
            "evt_bb_pr_acme_my-repo_99_1776",
            {"bb_repository": "acme/my-repo", "bb_comment_id": 123},
            {},
        )
        assert ids == {
            "workspace": "acme",
            "repo": "my-repo",
            "pr_id": "99",
            "comment_id": "123",
        }

    def test_gmail_id_and_reply_to(self):
        ids = gmail.identifiers_from_event(
            "send_email",
            "evt_gmail_msgABC",
            {"gmail_thread_id": "thr-1"},
            {"actor_email": "sender@co.com"},
        )
        assert ids["gmail_id"] == "msgABC"
        assert ids["thread_id"] == "thr-1"
        assert ids["to"] == "sender@co.com"

    def test_gmail_self_reply_guard(self):
        ids = gmail.identifiers_from_event(
            "send_email",
            "evt_gmail_x",
            {},
            {"actor_email": "me@co.com"},
            self_emails={"me@co.com"},
        )
        assert "to" not in ids  # don't self-reply

    def test_gmail_forward_does_not_default_to_sender(self):
        # forward recipients are user-chosen; actor_email should NOT be set as "to".
        ids = gmail.identifiers_from_event(
            "forward", "evt_gmail_x", {}, {"actor_email": "sender@co.com"}
        )
        assert "to" not in ids

    def test_outlook_id_and_conversation(self):
        ids = outlook.identifiers_from_event(
            "archive",
            "evt_outlook_msgXYZ",
            {"outlook_conversation_id": "conv-1"},
            {},
        )
        assert ids["outlook_id"] == "msgXYZ"
        assert ids["conversation_id"] == "conv-1"

    def test_slack_channel_and_thread(self):
        ids = slack.identifiers_from_event(
            "reply_thread",
            "evt_slack_1776.0001",
            {"slack_channel": "C1", "slack_thread_ts": "1776.0001"},
            {},
        )
        assert ids == {"channel": "C1", "thread_ts": "1776.0001"}

    def test_slack_react_includes_timestamp(self):
        ids = slack.identifiers_from_event(
            "react",
            "evt_slack_1776.0002",
            {"slack_channel": "C1"},
            {},
        )
        assert ids == {"channel": "C1", "timestamp": "1776.0002"}

    def test_calendar_returns_empty_for_create(self):
        ids = calendar.identifiers_from_event("create_event", "evt_cal_x", {}, {})
        assert ids == {}
