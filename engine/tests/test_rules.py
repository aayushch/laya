# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the RULES ENGINE pipeline step."""

from unittest.mock import patch

import pytest

from laya.pipeline.rules import _evaluate_condition, _get_field_value, run_rules
from laya.models.rules import AllCondition, AnyCondition, SimpleCondition


# --- Field extraction ---


def test_get_field_actor_email(sample_event):
    assert _get_field_value(sample_event, "actor.email") == "sarah@company.com"


def test_get_field_source_platform(sample_event):
    assert _get_field_value(sample_event, "source.platform") == "jira"


def test_get_field_nested_metadata(slack_event):
    assert _get_field_value(slack_event, "content.metadata.slack_channel") == "random"


def test_get_field_missing_returns_none(sample_event):
    assert _get_field_value(sample_event, "nonexistent.field") is None


# --- Simple conditions ---


def test_simple_equals_match(sample_event):
    cond = SimpleCondition(field="source.platform", operator="equals", value="jira")
    assert _evaluate_condition(cond, sample_event) is True


def test_simple_equals_no_match(sample_event):
    cond = SimpleCondition(field="source.platform", operator="equals", value="slack")
    assert _evaluate_condition(cond, sample_event) is False


def test_simple_not_equals(sample_event):
    cond = SimpleCondition(field="source.platform", operator="not_equals", value="slack")
    assert _evaluate_condition(cond, sample_event) is True


def test_simple_contains(bot_event):
    cond = SimpleCondition(field="actor.email", operator="contains", value="bot")
    assert _evaluate_condition(cond, bot_event) is True


def test_simple_starts_with(sample_event):
    cond = SimpleCondition(field="actor.email", operator="starts_with", value="sarah")
    assert _evaluate_condition(cond, sample_event) is True


def test_simple_ends_with(sample_event):
    cond = SimpleCondition(field="actor.email", operator="ends_with", value="company.com")
    assert _evaluate_condition(cond, sample_event) is True


def test_simple_in_operator(sample_event):
    cond = SimpleCondition(field="source.platform", operator="in", value=["jira", "slack"])
    assert _evaluate_condition(cond, sample_event) is True


def test_case_insensitive_matching(sample_event):
    cond = SimpleCondition(field="actor.email", operator="equals", value="SARAH@COMPANY.COM")
    assert _evaluate_condition(cond, sample_event) is True


# --- Compound conditions ---


def test_compound_all_match(slack_event):
    cond = AllCondition(
        all=[
            SimpleCondition(field="source.platform", operator="equals", value="slack"),
            SimpleCondition(field="content.metadata.slack_channel", operator="equals", value="random"),
        ]
    )
    assert _evaluate_condition(cond, slack_event) is True


def test_compound_all_partial_miss(slack_event):
    cond = AllCondition(
        all=[
            SimpleCondition(field="source.platform", operator="equals", value="slack"),
            SimpleCondition(field="content.metadata.slack_channel", operator="equals", value="engineering"),
        ]
    )
    assert _evaluate_condition(cond, slack_event) is False


def test_compound_any_match(sample_event):
    cond = AnyCondition(
        any=[
            SimpleCondition(field="source.platform", operator="equals", value="slack"),
            SimpleCondition(field="source.platform", operator="equals", value="jira"),
        ]
    )
    assert _evaluate_condition(cond, sample_event) is True


# --- run_rules integration ---


@pytest.mark.asyncio
async def test_disabled_rule_skipped(db, sample_event):
    rules = {
        "rules": [
            {
                "name": "Disabled rule",
                "enabled": False,
                "condition": {"field": "source.platform", "operator": "equals", "value": "jira"},
                "action": "drop",
            }
        ]
    }
    # Insert event
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sample_event.event_id, "2026-02-22", "jira", "issue_assigned", "ticket", "BUG-1234", "{}"),
    )
    await db.commit()

    with patch("laya.pipeline.rules.load_rules", return_value=rules):
        filtered, rule = await run_rules(sample_event)
        assert filtered is False
        assert rule is None


@pytest.mark.asyncio
async def test_run_rules_filters_and_updates_db(db, bot_event, mock_rules):
    # Insert event
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bot_event.event_id, "2026-02-22", "jira", "issue_updated", "ticket", "BUG-1234", "{}"),
    )
    await db.commit()

    filtered, rule = await run_rules(bot_event)
    assert filtered is True
    assert rule == "Ignore bot messages"

    async with db.execute(
        "SELECT filtered, filter_rule FROM events WHERE event_id=?", (bot_event.event_id,)
    ) as cursor:
        row = await cursor.fetchone()
        assert row[0] == 1  # filtered=TRUE
        assert row[1] == "Ignore bot messages"


@pytest.mark.asyncio
async def test_no_rules_match_passes(db, sample_event, mock_rules):
    # Sarah is not a bot, and this is jira not slack — should pass all rules
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sample_event.event_id, "2026-02-22", "jira", "issue_assigned", "ticket", "BUG-1234", "{}"),
    )
    await db.commit()

    filtered, rule = await run_rules(sample_event)
    assert filtered is False
    assert rule is None


@pytest.mark.asyncio
async def test_compound_rule_filters_slack_random(db, slack_event, mock_rules):
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slack_event.event_id, "2026-02-22", "slack", "message_received", "thread", "thread-random", "{}"),
    )
    await db.commit()

    filtered, rule = await run_rules(slack_event)
    assert filtered is True
    assert rule == "Mute #random"
