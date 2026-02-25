"""Tests for the learning loop — feedback query and prompt injection."""

import json
from datetime import datetime, timezone

import pytest

from laya.pipeline.feedback import format_feedback_section, query_feedback_patterns


async def _insert_event(db, event_id, platform="jira", event_type="issue_assigned"):
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, "2026-02-22T14:30:00Z", platform, event_type,
         "ticket", "BUG-1", "Test", "{}"),
    )
    await db.commit()


async def _insert_card(db, card_id, event_id, persona="ENGINEER", priority="HIGH",
                       status="approved", user_feedback=None):
    await db.execute(
        "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
        "header, summary, status, privacy_tier, resolved_at, user_feedback) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, event_id, priority, persona, "CODE", "Header", "Summary",
         status, 1, "2026-02-22T15:00:00Z" if status in ("approved", "dismissed") else None,
         user_feedback),
    )
    await db.commit()


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
    async def test_no_patterns_when_empty(self, db_m7, jira_event):
        """Returns empty list when no feedback data exists."""
        patterns = await query_feedback_patterns(jira_event)
        assert patterns == []

    async def test_returns_approved_patterns(self, db_m7, jira_event):
        """Returns patterns for approved cards."""
        await _insert_event(db_m7, "evt_1")
        await _insert_card(db_m7, "card_1", "evt_1", status="approved")
        await _insert_card(db_m7, "card_2", "evt_1", status="approved")

        patterns = await query_feedback_patterns(jira_event)
        assert len(patterns) >= 1
        assert patterns[0]["status"] == "approved"
        assert patterns[0]["count"] == 2

    async def test_returns_dismissed_patterns(self, db_m7, jira_event):
        """Returns patterns for dismissed cards."""
        await _insert_event(db_m7, "evt_d1")
        await _insert_card(db_m7, "card_d1", "evt_d1", status="dismissed")

        patterns = await query_feedback_patterns(jira_event)
        assert len(patterns) == 1
        assert patterns[0]["status"] == "dismissed"
        assert patterns[0]["count"] == 1

    async def test_platform_filtering(self, db_m7, jira_event, github_event):
        """Only returns patterns matching the event's platform."""
        await _insert_event(db_m7, "evt_jira", platform="jira")
        await _insert_event(db_m7, "evt_gh", platform="github")
        await _insert_card(db_m7, "card_j", "evt_jira", status="approved")
        await _insert_card(db_m7, "card_g", "evt_gh", status="approved")

        jira_patterns = await query_feedback_patterns(jira_event)
        assert all(p["source_platform"] == "jira" for p in jira_patterns)

        gh_patterns = await query_feedback_patterns(github_event)
        assert all(p["source_platform"] == "github" for p in gh_patterns)

    async def test_excludes_pending_cards(self, db_m7, jira_event):
        """Pending cards are not included in patterns."""
        await _insert_event(db_m7, "evt_p")
        await _insert_card(db_m7, "card_pending", "evt_p", status="pending")

        patterns = await query_feedback_patterns(jira_event)
        assert patterns == []

    async def test_limit_parameter(self, db_m7, jira_event):
        """Limit parameter caps result count."""
        for i in range(5):
            eid = f"evt_lim_{i}"
            await _insert_event(db_m7, eid, event_type=f"type_{i}")
            await _insert_card(db_m7, f"card_lim_{i}", eid, status="approved")

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
             "persona": "ENGINEER", "priority": "HIGH", "status": "approved", "count": 7},
            {"source_platform": "jira", "event_type": "issue_assigned",
             "persona": "ENGINEER", "priority": "HIGH", "status": "dismissed", "count": 3},
        ]
        result = format_feedback_section(patterns)
        assert result is not None
        assert "USER FEEDBACK PATTERNS" in result
        assert "jira/issue_assigned" in result
        assert "Approved 7/10" in result
        assert "Dismissed 3/10" in result
        assert "70% approval rate" in result
        assert "END FEEDBACK" in result

    def test_multiple_groups(self):
        """Formats multiple platform/event_type groups."""
        patterns = [
            {"source_platform": "jira", "event_type": "issue_assigned",
             "persona": "ENGINEER", "priority": "HIGH", "status": "approved", "count": 5},
            {"source_platform": "jira", "event_type": "issue_commented",
             "persona": "COMMS", "priority": "LOW", "status": "dismissed", "count": 3},
        ]
        result = format_feedback_section(patterns)
        assert "jira/issue_assigned" in result
        assert "jira/issue_commented" in result
