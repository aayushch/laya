"""Tests for the processing rules engine (laya.pipeline.processing_rules)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from laya.models.classification import Category, Persona, Priority, RouterOutput
from laya.models.event import LayaEvent
from laya.models.processing_rules import (
    BookmarkAction,
    ProcessingAllCondition,
    ProcessingAnyCondition,
    ProcessingNotCondition,
    ProcessingSimpleCondition,
    SendNotificationAction,
    SetPriorityAction,
    SetStatusAction,
)
from laya.pipeline.processing_rules import (
    _check_rate_limits,
    _execute_action,
    _parse_actions,
    _parse_condition,
    _resolve_template,
    build_trigger_context,
    evaluate_condition,
    run_processing_rules,
)
from tests.conftest import insert_test_card, insert_test_event


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_processing_event() -> LayaEvent:
    """A LayaEvent suitable for processing rules testing."""
    return LayaEvent(
        event_id="evt_proc_001",
        timestamp=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        source={"platform": "jira", "raw_event_type": "issue_created", "connection_id": "conn_jira_1"},
        actor={"name": "Alice Dev", "email": "alice@company.com", "platform_handle": "alice.dev"},
        subject={"type": "ticket", "id": "PROJ-42", "title": "Login page broken", "url": "https://jira.example.com/PROJ-42"},
        content={"body": "Users cannot log in after latest deploy", "attachments": [], "metadata": {"priority_field": "P1"}},
    )


@pytest.fixture
def sample_router_output() -> RouterOutput:
    """A RouterOutput for processing rules testing."""
    return RouterOutput(
        category=Category.CODE,
        persona=Persona.ENGINEER,
        priority=Priority.HIGH,
        confidence=0.9,
        entities=[],
        requires_research=True,
        reasoning="Login page bug needs investigation.",
    )


@pytest.fixture
def sample_context(sample_processing_event, sample_router_output) -> dict:
    """A pre-built trigger context dict."""
    return build_trigger_context(
        event=sample_processing_event,
        router_output=sample_router_output,
        card_id="card_proc_001",
        entity_id="jira:ticket:PROJ-42",
        space_id="default",
        actor_relationship="teammate",
        is_carry_forward=False,
        entity_card_count=3,
    )


# ---------------------------------------------------------------------------
# 1. Condition evaluation — simple operators
# ---------------------------------------------------------------------------

class TestSimpleConditions:
    def test_equals(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira")
        assert evaluate_condition(cond, sample_context) is True

    def test_equals_case_insensitive(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="JIRA")
        assert evaluate_condition(cond, sample_context) is True

    def test_equals_mismatch(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack")
        assert evaluate_condition(cond, sample_context) is False

    def test_not_equals(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="not_equals", value="slack")
        assert evaluate_condition(cond, sample_context) is True

    def test_contains(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.title", operator="contains", value="broken")
        assert evaluate_condition(cond, sample_context) is True

    def test_not_contains(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.title", operator="not_contains", value="database")
        assert evaluate_condition(cond, sample_context) is True

    def test_starts_with(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.title", operator="starts_with", value="login")
        assert evaluate_condition(cond, sample_context) is True

    def test_ends_with(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.title", operator="ends_with", value="broken")
        assert evaluate_condition(cond, sample_context) is True

    def test_in_list(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="in", value=["jira", "slack", "gmail"])
        assert evaluate_condition(cond, sample_context) is True

    def test_in_list_no_match(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="in", value=["slack", "gmail"])
        assert evaluate_condition(cond, sample_context) is False

    def test_not_in_list(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="not_in", value=["slack", "gmail"])
        assert evaluate_condition(cond, sample_context) is True

    def test_matches_regex(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.id", operator="matches", value=r"^PROJ-\d+$")
        assert evaluate_condition(cond, sample_context) is True

    def test_matches_regex_no_match(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.subject.id", operator="matches", value=r"^BUG-\d+$")
        assert evaluate_condition(cond, sample_context) is False

    def test_gt(self, sample_context):
        cond = ProcessingSimpleCondition(field="classification.confidence", operator="gt", value=0.5)
        assert evaluate_condition(cond, sample_context) is True

    def test_gte(self, sample_context):
        cond = ProcessingSimpleCondition(field="classification.confidence", operator="gte", value=0.9)
        assert evaluate_condition(cond, sample_context) is True

    def test_lt(self, sample_context):
        cond = ProcessingSimpleCondition(field="classification.confidence", operator="lt", value=0.95)
        assert evaluate_condition(cond, sample_context) is True

    def test_lte(self, sample_context):
        cond = ProcessingSimpleCondition(field="classification.confidence", operator="lte", value=0.9)
        assert evaluate_condition(cond, sample_context) is True

    def test_gt_priority_ordinal(self, sample_context):
        """gt/gte/lt/lte support priority name ordinals (LOW=0, MEDIUM=1, HIGH=2, CRITICAL=3)."""
        cond = ProcessingSimpleCondition(field="classification.priority", operator="gt", value="MEDIUM")
        assert evaluate_condition(cond, sample_context) is True

    def test_lt_priority_ordinal(self, sample_context):
        cond = ProcessingSimpleCondition(field="classification.priority", operator="lt", value="CRITICAL")
        assert evaluate_condition(cond, sample_context) is True

    def test_exists(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.connection_id", operator="exists")
        assert evaluate_condition(cond, sample_context) is True

    def test_not_exists(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.actor.nonexistent_field", operator="not_exists")
        assert evaluate_condition(cond, sample_context) is True

    def test_exists_on_missing_field(self, sample_context):
        cond = ProcessingSimpleCondition(field="no.such.path", operator="exists")
        assert evaluate_condition(cond, sample_context) is False

    def test_not_exists_on_present_field(self, sample_context):
        cond = ProcessingSimpleCondition(field="event.source.platform", operator="not_exists")
        assert evaluate_condition(cond, sample_context) is False


# ---------------------------------------------------------------------------
# 1b. Condition evaluation — edge cases
# ---------------------------------------------------------------------------

class TestConditionEdgeCases:
    def test_none_value_returns_false_for_comparison(self, sample_context):
        """When the context value is None, non-exists operators return False."""
        cond = ProcessingSimpleCondition(field="no.such.path", operator="equals", value="anything")
        assert evaluate_condition(cond, sample_context) is False

    def test_empty_string_equals_empty(self):
        ctx = {"field": ""}
        cond = ProcessingSimpleCondition(field="field", operator="equals", value="")
        assert evaluate_condition(cond, ctx) is True

    def test_malformed_regex_returns_false(self, sample_context):
        """Invalid regex pattern should return False gracefully, not raise."""
        cond = ProcessingSimpleCondition(field="event.subject.id", operator="matches", value="[invalid(")
        assert evaluate_condition(cond, sample_context) is False


# ---------------------------------------------------------------------------
# 2. Composite conditions: all, any, not, nested
# ---------------------------------------------------------------------------

class TestCompositeConditions:
    def test_all_both_true(self, sample_context):
        cond = ProcessingAllCondition(all=[
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira"),
            ProcessingSimpleCondition(field="classification.priority", operator="equals", value="HIGH"),
        ])
        assert evaluate_condition(cond, sample_context) is True

    def test_all_one_false(self, sample_context):
        cond = ProcessingAllCondition(all=[
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira"),
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack"),
        ])
        assert evaluate_condition(cond, sample_context) is False

    def test_any_one_true(self, sample_context):
        cond = ProcessingAnyCondition(any=[
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack"),
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira"),
        ])
        assert evaluate_condition(cond, sample_context) is True

    def test_any_all_false(self, sample_context):
        cond = ProcessingAnyCondition(any=[
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack"),
            ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="gmail"),
        ])
        assert evaluate_condition(cond, sample_context) is False

    def test_not_negates(self, sample_context):
        inner = ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack")
        cond = ProcessingNotCondition(not_=inner)
        assert evaluate_condition(cond, sample_context) is True

    def test_not_true_becomes_false(self, sample_context):
        inner = ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira")
        cond = ProcessingNotCondition(not_=inner)
        assert evaluate_condition(cond, sample_context) is False

    def test_nested_all_containing_any(self, sample_context):
        """all[ any[...], simple ] — tests nesting."""
        cond = ProcessingAllCondition(all=[
            ProcessingAnyCondition(any=[
                ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="slack"),
                ProcessingSimpleCondition(field="event.source.platform", operator="equals", value="jira"),
            ]),
            ProcessingSimpleCondition(field="classification.priority", operator="equals", value="HIGH"),
        ])
        assert evaluate_condition(cond, sample_context) is True

    def test_nested_not_inside_all(self, sample_context):
        cond = ProcessingAllCondition(all=[
            ProcessingNotCondition(not_=ProcessingSimpleCondition(
                field="event.source.platform", operator="equals", value="slack"
            )),
            ProcessingSimpleCondition(field="classification.persona", operator="equals", value="ENGINEER"),
        ])
        assert evaluate_condition(cond, sample_context) is True

    def test_deeply_nested_conditions(self):
        """Deeply nested conditions (>32 levels) should not blow the stack.

        Python's default recursion limit is 1000, so 33 levels of nesting
        should still work. This test just verifies very deep nesting works.
        """
        # Build 33-deep nested not conditions wrapping a simple true condition
        ctx = {"val": "yes"}
        inner = ProcessingSimpleCondition(field="val", operator="equals", value="yes")
        # 32 layers of not: even number of negations = True
        for _ in range(32):
            inner = ProcessingNotCondition(not_=inner)
        assert evaluate_condition(inner, ctx) is True


# ---------------------------------------------------------------------------
# 3. Template variable resolution
# ---------------------------------------------------------------------------

class TestTemplateResolution:
    def test_simple_variable(self, sample_context):
        result = _resolve_template("Platform: {{event.source.platform}}", sample_context)
        assert result == "Platform: jira"

    def test_nested_path(self, sample_context):
        result = _resolve_template("Title: {{event.subject.title}}", sample_context)
        assert result == "Title: Login page broken"

    def test_missing_key(self, sample_context):
        """Missing key should substitute <missing:path> sentinel."""
        result = _resolve_template("Val: {{no.such.key}}", sample_context)
        assert result == "Val: <missing:no.such.key>"

    def test_multiple_variables(self, sample_context):
        result = _resolve_template(
            "{{event.source.platform}}/{{event.subject.id}}: {{classification.priority}}",
            sample_context,
        )
        assert result == "jira/PROJ-42: HIGH"

    def test_no_variables(self):
        result = _resolve_template("plain text", {})
        assert result == "plain text"

    def test_empty_template(self, sample_context):
        result = _resolve_template("", sample_context)
        assert result == ""


# ---------------------------------------------------------------------------
# 4. Trigger context builder
# ---------------------------------------------------------------------------

class TestBuildTriggerContext:
    def test_context_shape(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id="ent_1",
            space_id="default",
        )
        # Top-level keys
        assert set(ctx.keys()) == {"event", "classification", "card", "context"}

    def test_event_source_fields(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id=None,
            space_id=None,
        )
        assert ctx["event"]["source"]["platform"] == "jira"
        assert ctx["event"]["source"]["raw_event_type"] == "issue_created"
        assert ctx["event"]["source"]["connection_id"] == "conn_jira_1"

    def test_actor_fields(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id=None,
            space_id=None,
        )
        assert ctx["event"]["actor"]["name"] == "Alice Dev"
        assert ctx["event"]["actor"]["email"] == "alice@company.com"

    def test_classification_fields(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id=None,
            space_id=None,
        )
        assert ctx["classification"]["persona"] == "ENGINEER"
        assert ctx["classification"]["priority"] == "HIGH"
        assert ctx["classification"]["category"] == "CODE"
        assert ctx["classification"]["confidence"] == 0.9
        assert ctx["classification"]["requires_research"] is True

    def test_card_fields(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_x",
            entity_id="ent_x",
            space_id="workspace_a",
        )
        assert ctx["card"]["card_id"] == "card_x"
        assert ctx["card"]["entity_id"] == "ent_x"
        assert ctx["card"]["space_id"] == "workspace_a"

    def test_context_metadata_defaults(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id=None,
            space_id=None,
        )
        assert ctx["context"]["actor_relationship"] == "unknown"
        assert ctx["context"]["entity_card_count"] == 1
        assert ctx["context"]["is_carry_forward"] is False
        assert isinstance(ctx["context"]["hour_of_day"], int)
        assert isinstance(ctx["context"]["day_of_week"], int)

    def test_context_metadata_custom(self, sample_processing_event, sample_router_output):
        ctx = build_trigger_context(
            event=sample_processing_event,
            router_output=sample_router_output,
            card_id="card_1",
            entity_id=None,
            space_id=None,
            actor_relationship="manager",
            is_carry_forward=True,
            entity_card_count=5,
        )
        assert ctx["context"]["actor_relationship"] == "manager"
        assert ctx["context"]["is_carry_forward"] is True
        assert ctx["context"]["entity_card_count"] == 5


# ---------------------------------------------------------------------------
# 5. Rate limit checks (needs DB)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRateLimits:
    async def test_no_limits_returns_none(self, db):
        """All zeros = no rate limiting."""
        result = await _check_rate_limits(rule_id=1, entity_id=None, rate_limit=0, cooldown_secs=0, max_daily=0)
        assert result is None

    async def test_hourly_rate_limit_not_exceeded(self, db):
        """Under the hourly limit returns None."""
        # Insert a rule and zero firings
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (100, "test_rule", '{"field":"a","operator":"equals","value":"b"}', '[]'),
        )
        await db.commit()
        result = await _check_rate_limits(rule_id=100, entity_id=None, rate_limit=5, cooldown_secs=0, max_daily=0)
        assert result is None

    async def test_hourly_rate_limit_exceeded(self, db):
        """At or above hourly limit returns skip reason."""
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (101, "rate_rule", '{"field":"a","operator":"equals","value":"b"}', '[]'),
        )
        # Insert parent cards then 3 firings within the last hour
        for i in range(3):
            await insert_test_card(db, f"card_{i}", f"evt_rate_{i}")
            await db.execute(
                "INSERT INTO processing_rule_firings (rule_id, card_id, fired_at) VALUES (?, ?, datetime('now'))",
                (101, f"card_{i}"),
            )
        await db.commit()

        result = await _check_rate_limits(rule_id=101, entity_id=None, rate_limit=3, cooldown_secs=0, max_daily=0)
        assert result is not None
        assert "hourly rate limit" in result

    async def test_entity_cooldown_not_triggered(self, db):
        """Entity cooldown with no recent firings returns None."""
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (102, "cooldown_rule", '{}', '[]'),
        )
        await db.commit()
        result = await _check_rate_limits(rule_id=102, entity_id="ent_1", rate_limit=0, cooldown_secs=300, max_daily=0)
        assert result is None

    async def test_entity_cooldown_triggered(self, db):
        """Entity cooldown with recent firing returns skip reason."""
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (103, "cooldown_rule2", '{"field":"a","operator":"equals","value":"b"}', '[]'),
        )
        await insert_test_card(db, "card_cd", "evt_cd")
        await db.execute(
            "INSERT INTO processing_rule_firings (rule_id, card_id, entity_id, fired_at) VALUES (?, ?, ?, datetime('now'))",
            (103, "card_cd", "ent_2"),
        )
        await db.commit()
        result = await _check_rate_limits(rule_id=103, entity_id="ent_2", rate_limit=0, cooldown_secs=600, max_daily=0)
        assert result is not None
        assert "entity cooldown" in result

    async def test_daily_cap_not_exceeded(self, db):
        """Under the daily cap returns None."""
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (104, "daily_rule", '{}', '[]'),
        )
        await db.commit()
        result = await _check_rate_limits(rule_id=104, entity_id=None, rate_limit=0, cooldown_secs=0, max_daily=10)
        assert result is None

    async def test_daily_cap_exceeded(self, db):
        """At or above daily cap returns skip reason."""
        await db.execute(
            "INSERT INTO processing_rules (id, name, condition_json, actions_json) VALUES (?, ?, ?, ?)",
            (105, "daily_cap_rule", '{"field":"a","operator":"equals","value":"b"}', '[]'),
        )
        for i in range(5):
            await insert_test_card(db, f"card_d{i}", f"evt_d{i}")
            await db.execute(
                "INSERT INTO processing_rule_firings (rule_id, card_id, fired_at) VALUES (?, ?, datetime('now'))",
                (105, f"card_d{i}"),
            )
        await db.commit()
        result = await _check_rate_limits(rule_id=105, entity_id=None, rate_limit=0, cooldown_secs=0, max_daily=5)
        assert result is not None
        assert "daily cap" in result


# ---------------------------------------------------------------------------
# 6. Action executors (needs DB + mocks)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestActionExecutors:
    async def test_set_status_dismissed(self, db):
        """_execute_action with SetStatusAction sets status to dismissed."""
        await insert_test_card(db, "card_st1", "evt_st1", status="pending")
        action = SetStatusAction(status="dismissed", reason="Auto-dismissed by rule")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock) as mock_bc:
            result = await _execute_action(action, "card_st1", None, None, {})

        assert result["success"] is True
        assert result["status"] == "dismissed"
        rows = await db.execute_fetchall("SELECT status, user_feedback FROM action_cards WHERE card_id = ?", ("card_st1",))
        assert rows[0]["status"] == "dismissed"
        assert rows[0]["user_feedback"] == "Auto-dismissed by rule"
        mock_bc.assert_called_once()

    async def test_set_status_archived(self, db):
        await insert_test_card(db, "card_st2", "evt_st2", status="pending")
        action = SetStatusAction(status="archived")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            result = await _execute_action(action, "card_st2", None, None, {})

        assert result["success"] is True
        rows = await db.execute_fetchall("SELECT status FROM action_cards WHERE card_id = ?", ("card_st2",))
        assert rows[0]["status"] == "archived"

    async def test_set_status_done(self, db):
        await insert_test_card(db, "card_st3", "evt_st3", status="pending")
        action = SetStatusAction(status="done")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            result = await _execute_action(action, "card_st3", None, None, {})

        assert result["success"] is True
        rows = await db.execute_fetchall("SELECT status FROM action_cards WHERE card_id = ?", ("card_st3",))
        assert rows[0]["status"] == "done"

    async def test_set_priority(self, db):
        await insert_test_card(db, "card_pr1", "evt_pr1", priority="MEDIUM")
        action = SetPriorityAction(priority="CRITICAL")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock) as mock_bc:
            result = await _execute_action(action, "card_pr1", None, None, {})

        assert result["success"] is True
        assert result["priority"] == "CRITICAL"
        rows = await db.execute_fetchall("SELECT priority FROM action_cards WHERE card_id = ?", ("card_pr1",))
        assert rows[0]["priority"] == "CRITICAL"
        mock_bc.assert_called_once()

    async def test_bookmark(self, db):
        await insert_test_card(db, "card_bk1", "evt_bk1")
        action = BookmarkAction()

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            result = await _execute_action(action, "card_bk1", None, None, {})

        assert result["success"] is True
        assert result["bookmarked"] is True
        rows = await db.execute_fetchall("SELECT bookmarked_at FROM action_cards WHERE card_id = ?", ("card_bk1",))
        assert rows[0]["bookmarked_at"] is not None

    async def test_bookmark_idempotent(self, db):
        """Bookmarking an already-bookmarked card does not overwrite the timestamp."""
        await insert_test_card(db, "card_bk2", "evt_bk2")
        # Set initial bookmark
        await db.execute("UPDATE action_cards SET bookmarked_at = '2026-01-01T00:00:00Z' WHERE card_id = ?", ("card_bk2",))
        await db.commit()

        action = BookmarkAction()
        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await _execute_action(action, "card_bk2", None, None, {})

        rows = await db.execute_fetchall("SELECT bookmarked_at FROM action_cards WHERE card_id = ?", ("card_bk2",))
        # Original timestamp preserved (WHERE bookmarked_at IS NULL prevents overwrite)
        assert rows[0]["bookmarked_at"] == "2026-01-01T00:00:00Z"

    async def test_notification(self, db):
        context = {"event": {"source": {"platform": "jira"}}, "event.subject": {"title": "Test"}}
        action = SendNotificationAction(
            title_template="New card from {{event.source.platform}}",
            body_template="Check it out",
        )

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock) as mock_bc:
            result = await _execute_action(action, "card_notif", None, None, context)

        assert result["success"] is True
        assert result["title"] == "New card from jira"
        mock_bc.assert_called_once()
        payload = mock_bc.call_args[0][0]
        assert payload["type"] == "push_notification"
        assert payload["payload"]["title"] == "New card from jira"
        assert payload["payload"]["card_id"] == "card_notif"

    async def test_unknown_action_type(self, db):
        """An unrecognized action type returns an error result."""

        class FakeAction:
            pass

        result = await _execute_action(FakeAction(), "card_x", None, None, {})
        assert result["success"] is False
        assert "Unknown action type" in result["error"]


# ---------------------------------------------------------------------------
# 7. Parsing helpers
# ---------------------------------------------------------------------------

class TestParseHelpers:
    def test_parse_simple_condition(self):
        cond = _parse_condition('{"field":"event.source.platform","operator":"equals","value":"jira"}')
        assert isinstance(cond, ProcessingSimpleCondition)
        assert cond.field == "event.source.platform"

    def test_parse_all_condition(self):
        data = json.dumps({"all": [
            {"field": "a", "operator": "equals", "value": "1"},
            {"field": "b", "operator": "equals", "value": "2"},
        ]})
        cond = _parse_condition(data)
        assert isinstance(cond, ProcessingAllCondition)
        assert len(cond.all) == 2

    def test_parse_any_condition(self):
        data = json.dumps({"any": [
            {"field": "x", "operator": "contains", "value": "y"},
        ]})
        cond = _parse_condition(data)
        assert isinstance(cond, ProcessingAnyCondition)

    def test_parse_not_condition(self):
        data = json.dumps({"not": {"field": "a", "operator": "equals", "value": "b"}})
        cond = _parse_condition(data)
        assert isinstance(cond, ProcessingNotCondition)

    def test_parse_actions_set_status(self):
        data = json.dumps([{"type": "set_status", "status": "dismissed", "reason": "test"}])
        actions = _parse_actions(data)
        assert len(actions) == 1
        assert isinstance(actions[0], SetStatusAction)
        assert actions[0].status == "dismissed"

    def test_parse_actions_multiple(self):
        data = json.dumps([
            {"type": "set_priority", "priority": "CRITICAL"},
            {"type": "bookmark"},
            {"type": "send_notification", "title_template": "t", "body_template": "b"},
        ])
        actions = _parse_actions(data)
        assert len(actions) == 3
        assert isinstance(actions[0], SetPriorityAction)
        assert isinstance(actions[1], BookmarkAction)
        assert isinstance(actions[2], SendNotificationAction)


# ---------------------------------------------------------------------------
# 8. Full rule evaluation (integration, needs DB)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRunProcessingRules:
    async def _ensure_space(self, db, space_id: str):
        """Create a space if it doesn't exist (for FK constraint)."""
        await db.execute(
            "INSERT OR IGNORE INTO spaces (space_id, name, is_default) VALUES (?, ?, 0)",
            (space_id, space_id),
        )
        await db.commit()

    async def _insert_rule(self, db, *, rule_id=1, name="test_rule",
                           condition_json='{"field":"event.source.platform","operator":"equals","value":"jira"}',
                           actions_json='[{"type":"set_priority","priority":"CRITICAL"}]',
                           space_id=None, enabled=1, rate_limit=0, cooldown_secs=0,
                           max_daily=0, error_count=0, position=0):
        if space_id:
            await self._ensure_space(db, space_id)
        await db.execute(
            """INSERT INTO processing_rules
               (id, name, condition_json, actions_json, space_id, enabled, position,
                rate_limit, cooldown_secs, max_daily, error_count, fire_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (rule_id, name, condition_json, actions_json, space_id, enabled,
             position, rate_limit, cooldown_secs, max_daily, error_count),
        )
        await db.commit()

    async def test_matching_rule_fires(self, db, sample_processing_event, sample_router_output):
        """A matching rule creates a firing record and increments fire_count."""
        await insert_test_card(db, "card_run1", "evt_proc_001", space_id=None)
        await self._insert_rule(db, rule_id=200, space_id=None)

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run1",
                entity_id="ent_1",
                space_id=None,
            )

        # Check firing record
        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 200")
        assert len(firings) == 1
        assert firings[0]["card_id"] == "card_run1"

        # Check fire_count incremented
        rules = await db.execute_fetchall("SELECT fire_count FROM processing_rules WHERE id = 200")
        assert rules[0]["fire_count"] == 1

    async def test_non_matching_rule_does_not_fire(self, db, sample_processing_event, sample_router_output):
        """A rule whose condition doesn't match creates no firing."""
        await insert_test_card(db, "card_run2", "evt_proc_001", space_id=None)
        # Condition requires platform=slack but event is jira
        await self._insert_rule(
            db, rule_id=201,
            condition_json='{"field":"event.source.platform","operator":"equals","value":"slack"}',
            space_id=None,
        )

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run2",
                space_id=None,
            )

        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 201")
        assert len(firings) == 0

    async def test_disabled_rule_skipped(self, db, sample_processing_event, sample_router_output):
        """Disabled rules are not loaded."""
        await insert_test_card(db, "card_run3", "evt_proc_001", space_id=None)
        await self._insert_rule(db, rule_id=202, enabled=0, space_id=None)

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run3",
                space_id=None,
            )

        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 202")
        assert len(firings) == 0

    async def test_loop_guard_prevents_re_entry(self, db, sample_processing_event, sample_router_output):
        """When _rule_triggered=True, processing is skipped entirely."""
        await insert_test_card(db, "card_run4", "evt_proc_001", space_id=None)
        await self._insert_rule(db, rule_id=203, space_id=None)

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run4",
                space_id=None,
                _rule_triggered=True,
            )

        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 203")
        assert len(firings) == 0

    async def test_auto_disable_after_consecutive_errors(self, db, sample_processing_event, sample_router_output):
        """After _AUTO_DISABLE_THRESHOLD consecutive errors, the rule is auto-disabled."""
        await insert_test_card(db, "card_run5", "evt_proc_001", space_id=None)

        # Action that will always fail: run_entity_agent with no entity_id in DB
        # Use a simpler approach: an egress action that will fail on import
        # Actually, set_status on a non-existent card won't fail — the UPDATE just matches zero rows.
        # Use execute_egress which will fail because the egress module won't be importable in test.
        fail_actions = json.dumps([{"type": "execute_egress", "platform": "fake", "action_type": "fake", "payload_template": {}}])

        await self._insert_rule(
            db, rule_id=204,
            actions_json=fail_actions,
            error_count=4,  # One more error will hit threshold of 5
            space_id=None,
        )

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock) as mock_bc:
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run5",
                space_id=None,
            )

        # Rule should be disabled
        rules = await db.execute_fetchall("SELECT enabled, error_count FROM processing_rules WHERE id = 204")
        assert rules[0]["enabled"] == 0
        assert rules[0]["error_count"] >= 5

        # Auto-disable broadcast should have been sent
        bc_calls = [call[0][0] for call in mock_bc.call_args_list]
        auto_disable_msgs = [c for c in bc_calls if c.get("type") == "processing_rule_auto_disabled"]
        assert len(auto_disable_msgs) == 1

    async def test_rate_limited_rule_skipped(self, db, sample_processing_event, sample_router_output):
        """A rule that hits rate limit is skipped without firing."""
        await insert_test_card(db, "card_run6", "evt_proc_001", space_id=None)
        await self._insert_rule(db, rule_id=205, rate_limit=1, space_id=None)

        # Pre-insert a firing within the last hour so the limit is hit
        await insert_test_card(db, "card_prev", "evt_prev")
        await db.execute(
            "INSERT INTO processing_rule_firings (rule_id, card_id, fired_at) VALUES (?, ?, datetime('now'))",
            (205, "card_prev"),
        )
        await db.commit()

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run6",
                space_id=None,
            )

        # No new firing should be created
        firings = await db.execute_fetchall(
            "SELECT * FROM processing_rule_firings WHERE rule_id = 205 AND card_id = 'card_run6'"
        )
        assert len(firings) == 0

    async def test_space_scoped_rule_matches_space(self, db, sample_processing_event, sample_router_output):
        """A rule with space_id only fires for cards in that space."""
        await insert_test_card(db, "card_run7", "evt_proc_001", space_id="workspace_a")
        await self._insert_rule(db, rule_id=206, space_id="workspace_a")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run7",
                space_id="workspace_a",
            )

        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 206")
        assert len(firings) == 1

    async def test_space_scoped_rule_ignored_for_different_space(self, db, sample_processing_event, sample_router_output):
        """A rule scoped to workspace_a does not fire for workspace_b."""
        await insert_test_card(db, "card_run8", "evt_proc_001", space_id="workspace_b")
        await self._insert_rule(db, rule_id=207, space_id="workspace_a")

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run8",
                space_id="workspace_b",
            )

        firings = await db.execute_fetchall("SELECT * FROM processing_rule_firings WHERE rule_id = 207")
        assert len(firings) == 0

    async def test_error_count_resets_on_success(self, db, sample_processing_event, sample_router_output):
        """A successful execution resets error_count to 0."""
        await insert_test_card(db, "card_run9", "evt_proc_001", space_id=None)
        await self._insert_rule(db, rule_id=208, error_count=3, space_id=None)

        with patch("laya.pipeline.processing_rules.manager.broadcast", new_callable=AsyncMock):
            await run_processing_rules(
                event=sample_processing_event,
                router_output=sample_router_output,
                card_id="card_run9",
                space_id=None,
            )

        rules = await db.execute_fetchall("SELECT error_count FROM processing_rules WHERE id = 208")
        assert rules[0]["error_count"] == 0


# ---------------------------------------------------------------------------
# 9. Regex DoS guard
# ---------------------------------------------------------------------------

class TestRegexSafety:
    def test_long_regex_pattern_handled(self, sample_context):
        """A very long regex pattern should not cause issues (just evaluates normally)."""
        long_pattern = "a" * 500
        cond = ProcessingSimpleCondition(field="event.subject.title", operator="matches", value=long_pattern)
        # Should return False without blowing up
        assert evaluate_condition(cond, sample_context) is False

    def test_catastrophic_backtracking_regex(self, sample_context):
        """Patterns prone to catastrophic backtracking should still return (may be slow but not crash)."""
        # This is a known ReDoS pattern, but with short input it should be fine
        cond = ProcessingSimpleCondition(
            field="event.subject.id",
            operator="matches",
            value=r"(a+)+b",
        )
        # PROJ-42 doesn't match, should return False quickly
        assert evaluate_condition(cond, sample_context) is False

    def test_invalid_regex_returns_false(self, sample_context):
        """Malformed regex returns False, not an exception."""
        cond = ProcessingSimpleCondition(field="event.subject.id", operator="matches", value="[")
        assert evaluate_condition(cond, sample_context) is False
