# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Processing rules engine — evaluates post-emit rules and executes actions."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.models.processing_rules import (
    AddTagAction,
    BookmarkAction,
    ExecuteEgressAction,
    ProcessingAllCondition,
    ProcessingAnyCondition,
    ProcessingCondition,
    ProcessingNotCondition,
    ProcessingRule,
    ProcessingSimpleCondition,
    RunEntityAgentAction,
    SendNotificationAction,
    SetPriorityAction,
    SetStatusAction,
)

log = structlog.get_logger()

_PRIORITY_ORDINAL = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

_processing_semaphore = asyncio.Semaphore(4)

_VAR_RE = re.compile(r"\{\{([\w.]+)\}\}")

# Auto-disable after this many consecutive errors (default; overridable via settings)
def _get_auto_disable_threshold() -> int:
    from laya.config import load_settings
    settings = load_settings()
    return settings.get("processing_rules", {}).get("auto_disable_threshold", 5)

# Max rules that can fire for a single card before we stop evaluating
_MAX_FIRINGS_PER_CARD = 5

# Per-entity locks to prevent concurrent agent spawns
_agent_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Template variable resolution
# ---------------------------------------------------------------------------

def _resolve_template(template: str, context: dict[str, Any]) -> str:
    """Replace {{field.path}} placeholders with values from the context."""
    def _replace(match: re.Match) -> str:
        path = match.group(1)
        value: Any = context
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        if value is None or value == "":
            log.warning("processing_rule_template_missing_key", key=path)
            return f"<missing:{path}>"
        return str(value)
    return _VAR_RE.sub(_replace, template)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _get_context_value(context: dict[str, Any], field_path: str) -> Any:
    """Extract a value from the flat trigger context dict using dot-notation."""
    parts = field_path.split(".")
    obj: Any = context
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


def _evaluate_simple(condition: ProcessingSimpleCondition, context: dict[str, Any]) -> bool:
    """Evaluate a single field/operator/value condition against the context."""
    op = condition.operator.value
    actual = _get_context_value(context, condition.field)

    # exists / not_exists don't need a value
    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None

    if actual is None:
        return False

    expected = condition.value
    actual_str = str(actual).lower()

    match op:
        case "equals":
            return actual_str == str(expected).lower()
        case "not_equals":
            return actual_str != str(expected).lower()
        case "contains":
            return str(expected).lower() in actual_str
        case "not_contains":
            return str(expected).lower() not in actual_str
        case "starts_with":
            return actual_str.startswith(str(expected).lower())
        case "ends_with":
            return actual_str.endswith(str(expected).lower())
        case "in":
            if isinstance(expected, list):
                return actual_str in [str(v).lower() for v in expected]
            return actual_str in str(expected).lower()
        case "not_in":
            if isinstance(expected, list):
                return actual_str not in [str(v).lower() for v in expected]
            return actual_str not in str(expected).lower()
        case "matches":
            pattern = str(expected)
            if len(pattern) > 500:
                log.warning("processing_rule_regex_too_long", length=len(pattern))
                return False
            try:
                return bool(re.search(pattern, str(actual), re.IGNORECASE))
            except re.error:
                return False
        case "gt" | "gte" | "lt" | "lte":
            return _compare_ordinal(op, actual, expected)
        case _:
            log.warning("unknown_processing_operator", operator=op)
            return False


def _compare_ordinal(op: str, actual: Any, expected: Any) -> bool:
    """Compare values ordinally. Supports priority names and numeric values."""
    actual_num = _to_ordinal(actual)
    expected_num = _to_ordinal(expected)
    if actual_num is None or expected_num is None:
        return False
    match op:
        case "gt":
            return actual_num > expected_num
        case "gte":
            return actual_num >= expected_num
        case "lt":
            return actual_num < expected_num
        case "lte":
            return actual_num <= expected_num
    return False


def _to_ordinal(value: Any) -> float | None:
    """Convert a value to a numeric ordinal for comparison."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).upper()
    if s in _PRIORITY_ORDINAL:
        return float(_PRIORITY_ORDINAL[s])
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


_MAX_CONDITION_DEPTH = 32


def evaluate_condition(condition: ProcessingCondition, context: dict[str, Any], *, _depth: int = 0) -> bool:
    """Recursively evaluate a processing rule condition tree."""
    if _depth > _MAX_CONDITION_DEPTH:
        log.warning("processing_rule_condition_too_deep", depth=_depth)
        return False
    if isinstance(condition, ProcessingSimpleCondition):
        return _evaluate_simple(condition, context)
    elif isinstance(condition, ProcessingAllCondition):
        return all(evaluate_condition(c, context, _depth=_depth + 1) for c in condition.all)
    elif isinstance(condition, ProcessingAnyCondition):
        return any(evaluate_condition(c, context, _depth=_depth + 1) for c in condition.any)
    elif isinstance(condition, ProcessingNotCondition):
        return not evaluate_condition(condition.not_, context, _depth=_depth + 1)
    return False


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_trigger_context(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str,
    entity_id: str | None,
    space_id: str | None,
    actor_relationship: str | None = None,
    is_carry_forward: bool = False,
    entity_card_count: int = 1,
) -> dict[str, Any]:
    """Build the flat trigger context dict from pipeline data."""
    now = datetime.now()
    return {
        "event": {
            "source": {
                "platform": event.source.platform,
                "raw_event_type": event.source.raw_event_type,
                "connection_id": getattr(event.source, "connection_id", None),
            },
            "actor": {
                "name": event.actor.name,
                "email": event.actor.email,
                "platform_handle": getattr(event.actor, "platform_handle", None),
            },
            "subject": {
                "type": event.subject.type,
                "id": event.subject.id,
                "title": event.subject.title,
                "url": event.subject.url,
            },
            "content": {
                "body": event.content.body,
                "metadata": event.content.metadata if hasattr(event.content, "metadata") else {},
            },
        },
        "classification": {
            "persona": router_output.persona.value if hasattr(router_output.persona, "value") else str(router_output.persona),
            "priority": router_output.priority.value if hasattr(router_output.priority, "value") else str(router_output.priority),
            "category": router_output.category.value if hasattr(router_output.category, "value") else str(router_output.category),
            "confidence": router_output.confidence,
            "requires_research": router_output.requires_research,
        },
        "card": {
            "card_id": card_id,
            "entity_id": entity_id,
            "space_id": space_id,
        },
        "context": {
            "actor_relationship": actor_relationship or "unknown",
            "entity_card_count": entity_card_count,
            "is_carry_forward": is_carry_forward,
            "hour_of_day": now.hour,
            "day_of_week": now.weekday(),
        },
    }


# ---------------------------------------------------------------------------
# Rate limit checks
# ---------------------------------------------------------------------------

async def _check_rate_limits(
    rule_id: int,
    entity_id: str | None,
    rate_limit: int,
    cooldown_secs: int,
    max_daily: int,
) -> str | None:
    """Check rate limits for a rule. Returns a skip reason or None."""
    if not rate_limit and not cooldown_secs and not max_daily:
        return None

    db = await get_db()

    if rate_limit > 0:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM processing_rule_firings WHERE rule_id = ? AND fired_at > datetime('now', '-1 hour')",
            (rule_id,),
        )
        if rows and rows[0]["cnt"] >= rate_limit:
            return f"hourly rate limit ({rate_limit}/hr)"

    if cooldown_secs > 0 and entity_id:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM processing_rule_firings WHERE rule_id = ? AND entity_id = ? AND fired_at > datetime('now', ? || ' seconds')",
            (rule_id, entity_id, f"-{cooldown_secs}"),
        )
        if rows and rows[0]["cnt"] > 0:
            return f"entity cooldown ({cooldown_secs}s)"

    if max_daily > 0:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM processing_rule_firings WHERE rule_id = ? AND fired_at > date('now', 'start of day')",
            (rule_id,),
        )
        if rows and rows[0]["cnt"] >= max_daily:
            return f"daily cap ({max_daily}/day)"

    return None


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------

async def _execute_action(
    action: Any,
    card_id: str,
    entity_id: str | None,
    space_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single processing rule action. Returns result dict."""
    try:
        if isinstance(action, SetStatusAction):
            return await _exec_set_status(action, card_id)
        elif isinstance(action, SetPriorityAction):
            return await _exec_set_priority(action, card_id)
        elif isinstance(action, BookmarkAction):
            return await _exec_bookmark(card_id)
        elif isinstance(action, RunEntityAgentAction):
            return await _exec_run_agent(action, entity_id, context)
        elif isinstance(action, ExecuteEgressAction):
            return await _exec_egress(action, card_id, space_id, context)
        elif isinstance(action, SendNotificationAction):
            return await _exec_notification(action, card_id, context)
        elif isinstance(action, AddTagAction):
            return await _exec_add_tag(action, card_id)
        else:
            return {"success": False, "error": f"Unknown action type: {type(action).__name__}"}
    except Exception as e:
        log.error("processing_rule_action_error", action_type=type(action).__name__, error=str(e))
        return {"success": False, "error": str(e)}


async def _exec_set_status(action: SetStatusAction, card_id: str) -> dict[str, Any]:
    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(
            card_id, action.status,
            actor="processing_rule",
            reason=action.reason,
        )
        return {"success": True, "status": action.status}
    except ValueError as e:
        log.warning("processing_rule_invalid_transition", card_id=card_id, error=str(e))
        return {"success": False, "error": str(e)}


async def _exec_set_priority(action: SetPriorityAction, card_id: str) -> dict[str, Any]:
    db = await get_db()
    await db.execute(
        "UPDATE action_cards SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (action.priority, card_id),
    )
    await db.commit()
    await manager.broadcast({"type": "card_updated", "card_id": card_id, "payload": {"priority": action.priority}})
    return {"success": True, "priority": action.priority}


async def _exec_bookmark(card_id: str) -> dict[str, Any]:
    db = await get_db()
    now = db_now()
    await db.execute(
        "UPDATE action_cards SET bookmarked_at = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ? AND bookmarked_at IS NULL",
        (now, card_id),
    )
    await db.commit()
    await manager.broadcast({"type": "card_updated", "card_id": card_id, "payload": {"bookmarked": True}})
    return {"success": True, "bookmarked": True}


async def _exec_run_agent(
    action: RunEntityAgentAction,
    entity_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not entity_id:
        return {"success": False, "error": "No entity_id for agent run"}
    prompt = _resolve_template(action.prompt_template, context) if action.prompt_template else None
    lock = _agent_locks.setdefault(entity_id, asyncio.Lock())
    async with lock:
        try:
            from laya.agents.entity_context import (
                build_entity_agent_prompt,
                get_entity_research_dir,
                write_entity_context_file,
            )
            from laya.agents import session_manager
            from laya.config import load_repos
            from laya.workers.engineer import resolve_repo_path
            from laya.models.classification import Category, Persona, Priority, RouterOutput as RO
            from laya.api.cards_api import _stream_entity_agent
            from laya.tasks import create_task as create_tracked_task

            # Reuse the entity's existing workspace instead of spawning a duplicate.
            # include_terminal=True so a completed run (the common case — a manual
            # or prior rule run finishes its turn and the session is marked
            # 'completed') is resumed rather than replaced by a new session, which
            # would orphan the workspace the user sees via the Workspace button
            # (get_workspace returns the most-recent session for the entity).
            # Skip only while the agent is actively working or waiting on the user;
            # otherwise resume. Mirrors the manual flow in cards_api.run_entity_agent.
            existing = await session_manager.get_session_for_entity(entity_id, include_terminal=True)
            if existing:
                status = existing["status"]
                if status in ("starting", "running"):
                    return {"success": True, "skipped": True, "reason": "agent already running"}
                if status == "awaiting_input" or await session_manager.has_unanswered_questions(existing["session_id"]):
                    return {"success": True, "skipped": True, "reason": "workspace awaiting user input"}

            db = await get_db()
            card_rows = await db.execute_fetchall(
                "SELECT card_id, space_id FROM action_cards WHERE entity_id = ? ORDER BY created_at DESC",
                (entity_id,),
            )
            if not card_rows:
                return {"success": False, "error": "No cards for entity"}

            space_id = card_rows[0]["space_id"] or "default"
            anchor_card_id = card_rows[0]["card_id"]

            # Refresh CONTEXT.md so the agent (new or resumed) sees the latest cards.
            await write_entity_context_file(entity_id, space_id)
            research_dir_str = str(get_entity_research_dir(entity_id))

            dummy_router = RO(persona=Persona.ENGINEER, priority=Priority.MEDIUM, category=Category.CODE, confidence=0.8, entities=[])
            repo_path, other_repos = await resolve_repo_path(dummy_router, space_id=space_id)

            if repo_path:
                cwd = repo_path
                add_dirs = [research_dir_str] + [p for p in other_repos if p != research_dir_str]
            else:
                cwd = research_dir_str
                repos_data = load_repos()
                add_dirs = [r["path"] for r in repos_data.get("repos", []) if r.get("path")]

            # Spawn/resume the session FIRST. Only after it actually exists do
            # we flip the card to agent_running — the previous order set the
            # status via a raw UPDATE *before* the spawn, so a spawn failure
            # stranded the card in agent_running until the next restart sweep
            # (review §2 pipeline — P3-7). A failure here is caught by the outer
            # try/except and the card is left untouched.
            if existing:
                # Resume the same session (reuses session_id, so the Workspace
                # button keeps opening the workspace the user already had).
                resume_text = prompt or "Continue working. Check CONTEXT.md for updated entity context."
                agent = await session_manager.resume_conversation(
                    existing["session_id"], resume_text, add_dirs=add_dirs,
                )
                session_id = existing["session_id"]
            else:
                agent_prompt = build_entity_agent_prompt(entity_id, research_dir_str, repo_path, prompt)
                agent_type = session_manager.get_configured_agent_type()
                session_id, agent = await session_manager.start_session(
                    card_id=anchor_card_id, prompt=agent_prompt, repo_path=cwd,
                    agent_type=agent_type, space_id=space_id, add_dirs=add_dirs,
                    mode="plan", research=True, entity_id=entity_id,
                )

            now = db_now()
            await db.execute(
                "UPDATE action_cards SET has_workspace = 1, updated_at = ? WHERE entity_id = ?",
                (now, entity_id),
            )
            await db.commit()
            # Route the status flip through the lifecycle SSOT (validation +
            # atomic guard + broadcast) instead of a raw UPDATE.
            try:
                from laya.models.card_lifecycle import transition_card_status
                await transition_card_status(anchor_card_id, "agent_running", actor="processing_rule")
            except ValueError:
                # Anchor in a status that can't move to agent_running (e.g.
                # dismissed) — the workspace still exists; leave the card as-is.
                pass

            for card_row in card_rows:
                await manager.broadcast(
                    {"type": "card_updated", "card_id": card_row["card_id"], "payload": {"has_workspace": True}}
                )

            create_tracked_task(
                _stream_entity_agent(session_id=session_id, agent=agent, entity_id=entity_id, anchor_card_id=anchor_card_id),
                name=f"proc_rule_agent_{entity_id}",
            )

            return {"success": True, "session_id": session_id, "resumed": bool(existing)}
        except Exception as e:
            return {"success": False, "error": str(e)}


async def _exec_egress(
    action: ExecuteEgressAction,
    card_id: str,
    space_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    resolved_payload = {}
    for k, v in action.payload_template.items():
        resolved_payload[k] = _resolve_template(v, context) if isinstance(v, str) else v

    try:
        from laya.egress import route_and_execute
        from laya.egress.models import EgressRequest

        req = EgressRequest(
            card_id=card_id,
            platform=action.platform,
            action_type=action.action_type,
            payload=resolved_payload,
            connection_id=action.connection_id,
            space_id=space_id,
        )
        result = await route_and_execute(req)
        return {"success": result.success, "result": result.result_data if result.success else None, "error": result.error if not result.success else None}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _exec_notification(
    action: SendNotificationAction,
    card_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    title = _resolve_template(action.title_template, context)
    body = _resolve_template(action.body_template, context)
    await manager.broadcast({
        "type": "push_notification",
        "payload": {"title": title, "body": body, "card_id": card_id},
    })
    return {"success": True, "title": title}


async def _exec_add_tag(action: AddTagAction, card_id: str) -> dict[str, Any]:
    from laya.pipeline.tags import TAG_SOFT_CAP, update_card_tags_in_chromadb

    db = await get_db()
    tag_name = action.tag_name.strip().lower()
    if not tag_name:
        return {"success": False, "error": "Empty tag name"}

    rows = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (tag_name,))
    if rows:
        tag_id = rows[0]["tag_id"]
    elif action.create_if_missing:
        await db.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,))
        await db.commit()
        new = await db.execute_fetchall("SELECT tag_id FROM tags WHERE name = ?", (tag_name,))
        tag_id = new[0]["tag_id"]
    else:
        return {"success": False, "error": f"Tag '{tag_name}' not found"}

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM tag_assignments WHERE target_type = 'card' AND target_id = ?",
        (card_id,),
    )
    if count_rows[0]["cnt"] >= TAG_SOFT_CAP:
        return {"success": False, "error": f"Tag cap ({TAG_SOFT_CAP}) reached"}

    await db.execute(
        "INSERT OR IGNORE INTO tag_assignments (tag_id, target_type, target_id, assigned_by) VALUES (?, 'card', ?, 'rule')",
        (tag_id, card_id),
    )
    await db.commit()

    try:
        await update_card_tags_in_chromadb(card_id)
    except Exception as e:
        log.warning("rule_tag_chromadb_failed", card_id=card_id, error=str(e))

    await manager.broadcast({
        "type": "tags_changed",
        "payload": {"target_type": "card", "target_id": card_id, "tag_name": tag_name, "action": "assigned"},
    })
    return {"success": True, "tag_name": tag_name}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_processing_rules(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str,
    entity_id: str | None = None,
    space_id: str | None = None,
    actor_relationship: str | None = None,
    is_carry_forward: bool = False,
    entity_card_count: int = 1,
) -> None:
    """Evaluate all enabled processing rules against a newly emitted card.

    Called as a background task from run_emit(). Errors are logged and
    never propagate to the caller.
    """
    try:
        db = await get_db()

        # Load enabled rules for this space (global + space-scoped)
        if space_id:
            rows = await db.execute_fetchall(
                """SELECT id, name, condition_json, actions_json, space_id,
                          rate_limit, cooldown_secs, max_daily, error_count
                   FROM processing_rules
                   WHERE enabled = 1 AND (space_id IS NULL OR space_id = ?)
                   ORDER BY position ASC""",
                (space_id,),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, name, condition_json, actions_json, space_id,
                          rate_limit, cooldown_secs, max_daily, error_count
                   FROM processing_rules
                   WHERE enabled = 1 AND space_id IS NULL
                   ORDER BY position ASC""",
            )

        if not rows:
            return

        context = build_trigger_context(
            event, router_output, card_id, entity_id, space_id,
            actor_relationship, is_carry_forward, entity_card_count,
        )

        # Enrich context with card tags so rules can condition on them
        try:
            from laya.pipeline.tags import get_card_tag_names
            context["card"]["tags"] = await get_card_tag_names(card_id)
        except Exception:
            context["card"]["tags"] = []

        from laya.models.card_lifecycle import TERMINAL_STATUSES
        firings_this_card = 0
        card_terminated = False

        for rule_row in rows:
            if card_terminated:
                break
            if firings_this_card >= _MAX_FIRINGS_PER_CARD:
                log.info("processing_rules_per_card_cap", card_id=card_id, cap=_MAX_FIRINGS_PER_CARD)
                break

            rule_id = rule_row["id"]
            rule_name = rule_row["name"]

            try:
                condition = _parse_condition(rule_row["condition_json"])
                actions = _parse_actions(rule_row["actions_json"])
            except Exception as e:
                log.warning("processing_rule_parse_error", rule_id=rule_id, error=str(e))
                continue

            if not evaluate_condition(condition, context):
                continue

            # Rate limit check
            skip_reason = await _check_rate_limits(
                rule_id, entity_id,
                rule_row["rate_limit"], rule_row["cooldown_secs"], rule_row["max_daily"],
            )
            if skip_reason:
                log.info("processing_rule_rate_limited", rule_id=rule_id, rule=rule_name, reason=skip_reason)
                continue

            # Execute actions
            log.info("processing_rule_matched", rule_id=rule_id, rule=rule_name, card_id=card_id)
            results = []
            has_error = False

            for action in actions:
                result = await _execute_action(action, card_id, entity_id, space_id, context)
                results.append(result)
                if not result.get("success"):
                    has_error = True
                if isinstance(action, SetStatusAction) and result.get("success") and action.status in TERMINAL_STATUSES:
                    card_terminated = True
                    break

            firings_this_card += 1

            # Record firing
            await db.execute(
                """INSERT INTO processing_rule_firings
                   (rule_id, card_id, entity_id, event_id, actions_json, results_json, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id, card_id, entity_id, event.event_id,
                    json.dumps([a.model_dump() for a in actions]),
                    json.dumps(results),
                    results[-1].get("error") if has_error else None,
                ),
            )

            # Update rule stats
            new_error_count = (rule_row["error_count"] + 1) if has_error else 0
            auto_disable = new_error_count >= _get_auto_disable_threshold()

            await db.execute(
                """UPDATE processing_rules
                   SET fire_count = fire_count + 1,
                       last_fired_at = CURRENT_TIMESTAMP,
                       error_count = ?,
                       last_error = ?,
                       enabled = CASE WHEN ? THEN 0 ELSE enabled END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    new_error_count,
                    results[-1].get("error") if has_error else None,
                    auto_disable,
                    rule_id,
                ),
            )
            await db.commit()

            if auto_disable:
                log.warning("processing_rule_auto_disabled", rule_id=rule_id, rule=rule_name, consecutive_errors=new_error_count)
                await manager.broadcast({
                    "type": "processing_rule_auto_disabled",
                    "payload": {"rule_id": rule_id, "name": rule_name, "reason": f"{new_error_count} consecutive errors"},
                })

    except Exception as e:
        log.error("processing_rules_fatal", card_id=card_id, error=str(e))


def _parse_condition(condition_json: str) -> ProcessingCondition:
    """Parse a condition JSON string into the discriminated union type."""
    data = json.loads(condition_json) if isinstance(condition_json, str) else condition_json
    if "all" in data:
        return ProcessingAllCondition(**data)
    elif "any" in data:
        return ProcessingAnyCondition(**data)
    elif "not" in data:
        return ProcessingNotCondition(**data)
    else:
        return ProcessingSimpleCondition(**data)


def _parse_actions(actions_json: str) -> list:
    """Parse actions JSON into typed action objects."""
    from laya.models.processing_rules import ProcessingRuleAction
    from pydantic import TypeAdapter
    adapter = TypeAdapter(list[ProcessingRuleAction])
    data = json.loads(actions_json) if isinstance(actions_json, str) else actions_json
    return adapter.validate_python(data)
