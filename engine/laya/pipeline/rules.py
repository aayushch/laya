# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""RULES ENGINE pipeline step — evaluate event against filter rules."""

from __future__ import annotations

from typing import Any

import structlog

from laya.config import load_rules
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent
from laya.models.rules import (
    AllCondition,
    AnyCondition,
    RulesConfig,
    SimpleCondition,
)

log = structlog.get_logger()


def _get_field_value(event: LayaEvent, field_path: str) -> Any:
    """Extract a value from the event using dot-notation field path.

    Supports paths like "actor.email", "source.platform",
    "content.metadata.slack_channel".
    """
    parts = field_path.split(".")
    obj: Any = event

    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            return None

        if obj is None:
            return None

    return obj


def _evaluate_simple(condition: SimpleCondition, event: LayaEvent) -> bool:
    """Evaluate a single field/operator/value condition."""
    actual = _get_field_value(event, condition.field)
    if actual is None:
        return False

    actual_str = str(actual).lower()
    expected = condition.value

    match condition.operator.value:
        case "equals":
            return actual_str == str(expected).lower()
        case "not_equals":
            return actual_str != str(expected).lower()
        case "contains":
            return str(expected).lower() in actual_str
        case "starts_with":
            return actual_str.startswith(str(expected).lower())
        case "ends_with":
            return actual_str.endswith(str(expected).lower())
        case "in":
            if isinstance(expected, list):
                return actual_str in [str(v).lower() for v in expected]
            return actual_str in str(expected).lower()
        case _:
            log.warning("unknown_operator", operator=condition.operator)
            return False


def _evaluate_condition(condition: Any, event: LayaEvent) -> bool:
    """Recursively evaluate a condition (simple, all, or any)."""
    if isinstance(condition, SimpleCondition):
        return _evaluate_simple(condition, event)
    elif isinstance(condition, AllCondition):
        return all(_evaluate_condition(c, event) for c in condition.all)
    elif isinstance(condition, AnyCondition):
        return any(_evaluate_condition(c, event) for c in condition.any)
    return False


async def run_rules(event: LayaEvent) -> tuple[bool, str | None]:
    """Run the RULES ENGINE step: check event against all enabled rules.

    Returns (filtered: bool, matching_rule_name: str | None).
    If filtered=True, updates the event row in SQLite.
    """
    rules_data = load_rules()
    rules_config = RulesConfig(**rules_data)

    for rule in rules_config.rules:
        if not rule.enabled:
            continue

        if _evaluate_condition(rule.condition, event):
            if rule.action == "allow":
                log.info(
                    "event_allowed", event_id=event.event_id, rule=rule.name
                )
                return False, None

            # action == "drop"
            db = await get_db()
            await db.execute(
                "UPDATE events SET filtered = TRUE, filter_rule = ? WHERE event_id = ?",
                (rule.name, event.event_id),
            )
            await db.commit()

            log.info("event_filtered", event_id=event.event_id, rule=rule.name)
            return True, rule.name

    log.debug("event_passed_rules", event_id=event.event_id)
    return False, None
