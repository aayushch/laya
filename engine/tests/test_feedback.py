# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the learning loop — feedback query and prompt injection."""

import json
from datetime import datetime, timezone

import pytest

from laya.pipeline.feedback import format_feedback_section, query_feedback_patterns
from tests.conftest import insert_test_card, insert_test_event


@pytest.fixture
def jira_event():
    from laya.models.event import LayaEvent
    return LayaEvent(
        event_id="evt_fb_test",
        timestamp=datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc),
        source={"platform": "jira", "raw_event_type": "issue_assigned"},
        actor={"name": "Sarah", "email": "sarah@co.com"},
        subject={"type": "ticket", "id": "BUG-1", "title": "Test"},
        content={"body": "test", "attachments": [], "metadata": {}},
    )


@pytest.fixture
def github_event():
    from laya.models.event import LayaEvent
    return LayaEvent(
        event_id="evt_fb_gh",
        timestamp=datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc),
        source={"platform": "github", "raw_event_type": "pull_request_opened"},
        actor={"name": "Dev", "email": "dev@co.com"},
        subject={"type": "pull_request", "id": "PR-1", "title": "Test PR"},
        content={"body": "pr body", "attachments": [], "metadata": {}},
    )


@pytest.mark.asyncio
class TestQueryFeedbackPatterns:
    async def test_no_patterns_when_empty(self, db, jira_event):
        """Returns empty list when no feedback data exists."""
        patterns = await query_feedback_patterns(jira_event)
        assert patterns == []

    async def test_returns_done_patterns(self, db, jira_event):
        """Returns patterns for done cards."""
        await insert_test_event(db, "evt_1")
        await insert_test_card(db, "card_1", "evt_1", status="done")
        await insert_test_card(db, "card_2", "evt_1", status="done")

        # Set resolved_at so the feedback query picks them up
        await db.execute(
            "UPDATE action_cards SET resolved_at = '2026-02-22T15:00:00Z' WHERE card_id IN ('card_1', 'card_2')"
        )
        await db.commit()

        patterns = await query_feedback_patterns(jira_event)
        assert len(patterns) >= 1
        assert patterns[0]["status"] == "done"
        assert patterns[0]["count"] == 2

    async def test_returns_dismissed_patterns(self, db, jira_event):
        """Returns patterns for dismissed cards."""
        await insert_test_event(db, "evt_d1")
        await insert_test_card(db, "card_d1", "evt_d1", status="dismissed")

        # Set resolved_at
        await db.execute(
            "UPDATE action_cards SET resolved_at = '2026-02-22T15:00:00Z' WHERE card_id = 'card_d1'"
        )
        await db.commit()

        patterns = await query_feedback_patterns(jira_event)
        assert len(patterns) == 1
        assert patterns[0]["status"] == "dismissed"
        assert patterns[0]["count"] == 1

    async def test_platform_filtering(self, db, jira_event, github_event):
        """Only returns patterns matching the event's platform."""
        await insert_test_event(db, "evt_jira", platform="jira")
        await insert_test_event(db, "evt_gh", platform="github")
        await insert_test_card(db, "card_j", "evt_jira", status="done")
        await insert_test_card(db, "card_g", "evt_gh", status="done")

        # Set resolved_at
        await db.execute(
            "UPDATE action_cards SET resolved_at = '2026-02-22T15:00:00Z' WHERE card_id IN ('card_j', 'card_g')"
        )
        await db.commit()

        jira_patterns = await query_feedback_patterns(jira_event)
        assert all(p["source_platform"] == "jira" for p in jira_patterns)

        gh_patterns = await query_feedback_patterns(github_event)
        assert all(p["source_platform"] == "github" for p in gh_patterns)

    async def test_excludes_pending_cards(self, db, jira_event):
        """Pending cards are not included in patterns."""
        await insert_test_event(db, "evt_p")
        await insert_test_card(db, "card_pending", "evt_p", status="pending")

        patterns = await query_feedback_patterns(jira_event)
        assert patterns == []

    async def test_limit_parameter(self, db, jira_event):
        """Limit parameter caps result count."""
        for i in range(5):
            eid = f"evt_lim_{i}"
            await insert_test_event(db, eid, raw_event_type=f"type_{i}")
            await insert_test_card(db, f"card_lim_{i}", eid, status="done")

        # Set resolved_at for all
        await db.execute(
            "UPDATE action_cards SET resolved_at = '2026-02-22T15:00:00Z' WHERE card_id LIKE 'card_lim_%'"
        )
        await db.commit()

        patterns = await query_feedback_patterns(jira_event, limit=2)
        assert len(patterns) <= 2


class TestFormatFeedbackSection:
    def test_none_for_empty_patterns(self):
        """Returns None when no patterns provided."""
        assert format_feedback_section([]) is None

    def test_formats_approval_rate(self):
        """Formats patterns with approval rate correctly."""
        patterns = [
            {"source_platform": "jira", "event_type": "issue_assigned",
             "persona": "ENGINEER", "priority": "HIGH", "status": "done", "count": 7},
            {"source_platform": "jira", "event_type": "issue_assigned",
             "persona": "ENGINEER", "priority": "HIGH", "status": "dismissed", "count": 3},
        ]
        result = format_feedback_section(patterns)
        assert result is not None
        assert "USER FEEDBACK PATTERNS" in result
        assert "jira/issue_assigned" in result
        assert "70% approval rate" in result
        assert "END FEEDBACK" in result

    def test_multiple_groups(self):
        """Formats multiple platform/event_type groups."""
        patterns = [
            {"source_platform": "jira", "event_type": "issue_assigned",
             "persona": "ENGINEER", "priority": "HIGH", "status": "done", "count": 5},
            {"source_platform": "jira", "event_type": "issue_commented",
             "persona": "COMMS", "priority": "LOW", "status": "dismissed", "count": 3},
        ]
        result = format_feedback_section(patterns)
        assert "jira/issue_assigned" in result
        assert "jira/issue_commented" in result
