# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Rule management tool implementations for Laya chat."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from pydantic import TypeAdapter

from laya.api.websocket import manager
from laya.config import load_rules, save_rules
from laya.db.sqlite import get_db
from laya.models.processing_rules import (
    ProcessingCondition,
    ProcessingRuleAction,
)
from laya.models.rules import Rule, RulesConfig

log = structlog.get_logger()

_processing_condition_adapter = TypeAdapter(ProcessingCondition)
_processing_actions_adapter = TypeAdapter(list[ProcessingRuleAction])


async def _broadcast_rules_changed(rule_type: str) -> None:
    await manager.broadcast({"type": "rules_changed", "payload": {"rule_type": rule_type}})


async def _write_rules_audit(tool: str, action: str, detail: Any) -> None:
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO audit_log (log_id, step, success, metadata)
               VALUES (?, ?, ?, ?)""",
            (
                f"audit_{uuid.uuid4().hex[:12]}",
                "rules",
                True,
                json.dumps({"source": "chat", "tool": tool, "action": action, "detail": detail}),
            ),
        )
        await db.commit()
    except Exception as exc:
        log.warning("rules_audit_log_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


async def list_rules(rule_type: str | None = None, space_id: str | None = None) -> dict[str, Any]:
    """List existing rules, optionally filtered by type."""
    result: dict[str, Any] = {}

    if rule_type is None or rule_type == "filter":
        data = load_rules()
        rules = data.get("rules", [])
        result["filter_rules"] = [
            {"name": r["name"], "enabled": r.get("enabled", True), "condition": r["condition"], "action": r.get("action", "drop")}
            for r in rules
        ]

    if rule_type is None or rule_type == "classification":
        db = await get_db()
        clauses = []
        params: list[Any] = []
        if space_id is not None:
            clauses.append("space_id = ?")
            params.append(space_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await db.execute_fetchall(
            f"SELECT * FROM classification_rules{where} ORDER BY created_at DESC LIMIT 50",
            tuple(params),
        )
        result["classification_rules"] = [
            {"id": r["id"], "rule_text": r["rule_text"], "field": r["field"], "source": r["source"], "active": bool(r["active"])}
            for r in rows
        ]

    if rule_type is None or rule_type == "processing":
        db = await get_db()
        clauses = []
        params = []
        if space_id is not None:
            clauses.append("space_id = ?")
            params.append(space_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await db.execute_fetchall(
            f"SELECT * FROM processing_rules{where} ORDER BY position LIMIT 50",
            tuple(params),
        )
        result["processing_rules"] = [
            {
                "id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]),
                "condition": json.loads(r["condition_json"]), "actions": json.loads(r["actions_json"]),
                "fire_count": r["fire_count"],
            }
            for r in rows
        ]

    return result


async def get_rule_options(
    category: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """Discover available values for building rule conditions."""
    db = await get_db()
    result: dict[str, Any] = {}

    if category is None or category == "platforms":
        rows = await db.execute_fetchall(
            "SELECT DISTINCT source_platform FROM events WHERE source_platform IS NOT NULL ORDER BY source_platform"
        )
        platforms = [r["source_platform"] for r in rows if r["source_platform"]]
        result["platforms"] = platforms or ["jira", "github", "slack", "gmail", "bitbucket", "calendar", "linear", "outlook", "notion"]

    if category is None or category == "event_types":
        sql = "SELECT DISTINCT source_raw_event_type FROM events WHERE source_raw_event_type IS NOT NULL"
        params: list[Any] = []
        if platform:
            sql += " AND source_platform = ?"
            params.append(platform)
        sql += " ORDER BY source_raw_event_type"
        rows = await db.execute_fetchall(sql, tuple(params))
        result["event_types"] = [r["source_raw_event_type"] for r in rows if r["source_raw_event_type"]]

    if category is None or category == "metadata_fields":
        if platform:
            rows = await db.execute_fetchall(
                "SELECT content_metadata FROM events WHERE source_platform = ? AND content_metadata IS NOT NULL ORDER BY created_at DESC LIMIT 100",
                (platform,),
            )
            keys: dict[str, set[str]] = {}
            for row in rows:
                try:
                    meta = json.loads(row["content_metadata"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(meta, dict):
                    continue
                for k, v in meta.items():
                    if isinstance(v, (list, dict)):
                        continue
                    keys.setdefault(k, set())
                    if len(keys[k]) < 50:
                        keys[k].add(str(v))
            result["metadata_fields"] = {k: sorted(v) for k, v in sorted(keys.items())}
        elif category == "metadata_fields":
            result["metadata_fields"] = {"error": "platform parameter required for metadata_fields"}

    if category is None or category == "tags":
        rows = await db.execute_fetchall("SELECT name FROM tags ORDER BY name")
        result["tags"] = [r["name"] for r in rows]

    if category is None or category == "field_values":
        rows = await db.execute_fetchall(
            "SELECT DISTINCT subject_type FROM events WHERE subject_type IS NOT NULL ORDER BY subject_type"
        )
        result["field_values"] = {
            "subject_types": [r["subject_type"] for r in rows if r["subject_type"]],
            "personas": ["ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"],
            "priorities": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            "categories": ["CODE", "COMMS", "PEOPLE", "FINANCE", "OPS"],
            "actor_relationships": ["self", "team", "external", "bot"],
        }

    return result


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


async def create_filter_rule(
    name: str,
    field: str | None = None,
    operator: str | None = None,
    value: str | list[str] | None = None,
    condition: dict | None = None,
    action: str = "drop",
    enabled: bool = True,
) -> dict[str, Any]:
    """Create a pre-pipeline event filter rule in rules.json."""
    if condition is None:
        if not field or not operator:
            return {"error": "Provide field and operator for a simple condition, or a condition object for compound logic"}
        condition = {"field": field, "operator": operator, "value": value}

    rule_dict = {"name": name, "enabled": enabled, "condition": condition, "action": action}

    try:
        Rule.model_validate(rule_dict)
    except Exception as e:
        return {"error": f"Invalid rule: {e}"}

    data = load_rules()
    rules = data.get("rules", [])

    for existing in rules:
        if existing["name"] == name:
            return {"error": f"A filter rule named '{name}' already exists"}

    rules.append(rule_dict)
    data["rules"] = rules

    try:
        RulesConfig.model_validate(data)
    except Exception as e:
        return {"error": f"Invalid rules config: {e}"}

    save_rules(data)
    await _write_rules_audit("create_filter_rule", "created", {"name": name})
    await _broadcast_rules_changed("filter")
    log.info("filter_rule_created_via_chat", name=name)
    return {"status": "created", "rule": rule_dict}


async def create_classification_rule(
    rule_text: str,
    field: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Create a natural language classification guidance rule."""
    if field is not None and field not in ("priority", "persona"):
        return {"error": "field must be 'priority', 'persona', or omitted for general rules"}

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """INSERT INTO classification_rules (space_id, field, rule_text, source, active, created_at, updated_at)
           VALUES (?, ?, ?, 'manual', 1, ?, ?)""",
        (space_id, field, rule_text, now, now),
    )
    await db.commit()
    rule_id = cursor.lastrowid

    await _write_rules_audit("create_classification_rule", "created", {"id": rule_id, "rule_text": rule_text})
    await _broadcast_rules_changed("classification")
    log.info("classification_rule_created_via_chat", rule_id=rule_id)
    return {"status": "created", "id": rule_id, "rule_text": rule_text, "field": field}


async def create_processing_rule(
    name: str,
    condition: dict,
    actions: list[dict],
    description: str | None = None,
    space_id: str | None = None,
    enabled: bool = True,
    rate_limit: int = 0,
    cooldown_secs: int = 0,
    max_daily: int = 0,
) -> dict[str, Any]:
    """Create a post-emit automation (processing) rule."""
    try:
        parsed_condition = _processing_condition_adapter.validate_python(condition)
    except Exception as e:
        return {"error": f"Invalid condition: {e}"}

    try:
        parsed_actions = _processing_actions_adapter.validate_python(actions)
    except Exception as e:
        return {"error": f"Invalid actions: {e}"}

    if not parsed_actions:
        return {"error": "At least one action is required"}

    condition_json = parsed_condition.model_dump_json() if hasattr(parsed_condition, "model_dump_json") else json.dumps(condition)
    actions_json = json.dumps([a.model_dump() for a in parsed_actions])

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM processing_rules"
    )
    next_pos = rows[0]["next_pos"] if rows else 0

    cursor = await db.execute(
        """INSERT INTO processing_rules
           (name, description, space_id, enabled, position, condition_json, actions_json,
            rate_limit, cooldown_secs, max_daily)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, description, space_id, enabled, next_pos, condition_json, actions_json,
         rate_limit, cooldown_secs, max_daily),
    )
    await db.commit()
    rule_id = cursor.lastrowid

    await _write_rules_audit("create_processing_rule", "created", {"id": rule_id, "name": name})
    await _broadcast_rules_changed("processing")
    log.info("processing_rule_created_via_chat", rule_id=rule_id, name=name)
    return {
        "status": "created", "id": rule_id, "name": name,
        "condition": condition, "actions": actions,
    }


async def update_rule(
    rule_type: str,
    rule_id: str | int,
    enabled: bool | None = None,
    name: str | None = None,
    rule_text: str | None = None,
    field: str | None = None,
    condition: dict | None = None,
    actions: list[dict] | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    """Update an existing rule by type and ID."""

    if rule_type == "filter":
        data = load_rules()
        rules = data.get("rules", [])
        target = None
        for r in rules:
            if r["name"] == str(rule_id):
                target = r
                break
        if target is None:
            return {"error": f"Filter rule '{rule_id}' not found"}

        if name is not None:
            target["name"] = name
        if enabled is not None:
            target["enabled"] = enabled
        if condition is not None:
            target["condition"] = condition
        if action is not None:
            target["action"] = action

        try:
            RulesConfig.model_validate(data)
        except Exception as e:
            return {"error": f"Invalid rule after update: {e}"}

        save_rules(data)
        await _write_rules_audit("update_rule", "updated_filter", {"name": rule_id})
        await _broadcast_rules_changed("filter")
        return {"status": "updated", "rule_type": "filter", "rule": target}

    elif rule_type == "classification":
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id FROM classification_rules WHERE id = ?", (int(rule_id),)
        )
        if not rows:
            return {"error": f"Classification rule {rule_id} not found"}

        updates = []
        params: list[Any] = []
        if rule_text is not None:
            updates.append("rule_text = ?")
            params.append(rule_text)
        if field is not None:
            updates.append("field = ?")
            params.append(field)
        if enabled is not None:
            updates.append("active = ?")
            params.append(int(enabled))

        if not updates:
            return {"error": "No fields to update"}

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(int(rule_id))

        await db.execute(
            f"UPDATE classification_rules SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        await db.commit()
        await _write_rules_audit("update_rule", "updated_classification", {"id": rule_id})
        await _broadcast_rules_changed("classification")
        return {"status": "updated", "rule_type": "classification", "id": int(rule_id)}

    elif rule_type == "processing":
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM processing_rules WHERE id = ?", (int(rule_id),)
        )
        if not rows:
            return {"error": f"Processing rule {rule_id} not found"}

        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(int(enabled))
        if condition is not None:
            try:
                parsed = _processing_condition_adapter.validate_python(condition)
                updates.append("condition_json = ?")
                params.append(parsed.model_dump_json() if hasattr(parsed, "model_dump_json") else json.dumps(condition))
            except Exception as e:
                return {"error": f"Invalid condition: {e}"}
        if actions is not None:
            try:
                parsed_actions = _processing_actions_adapter.validate_python(actions)
                updates.append("actions_json = ?")
                params.append(json.dumps([a.model_dump() for a in parsed_actions]))
            except Exception as e:
                return {"error": f"Invalid actions: {e}"}

        if not updates:
            return {"error": "No fields to update"}

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(rule_id))

        await db.execute(
            f"UPDATE processing_rules SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        await db.commit()
        await _write_rules_audit("update_rule", "updated_processing", {"id": rule_id})
        await _broadcast_rules_changed("processing")
        return {"status": "updated", "rule_type": "processing", "id": int(rule_id)}

    else:
        return {"error": f"Unknown rule_type: {rule_type}. Must be 'filter', 'classification', or 'processing'"}


async def delete_rule(rule_type: str, rule_id: str | int) -> dict[str, Any]:
    """Delete an existing rule by type and ID."""

    if rule_type == "filter":
        data = load_rules()
        rules = data.get("rules", [])
        original_len = len(rules)
        data["rules"] = [r for r in rules if r["name"] != str(rule_id)]
        if len(data["rules"]) == original_len:
            return {"error": f"Filter rule '{rule_id}' not found"}
        save_rules(data)
        await _write_rules_audit("delete_rule", "deleted_filter", {"name": rule_id})
        await _broadcast_rules_changed("filter")
        return {"status": "deleted", "rule_type": "filter", "name": str(rule_id)}

    elif rule_type == "classification":
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id FROM classification_rules WHERE id = ?", (int(rule_id),)
        )
        if not rows:
            return {"error": f"Classification rule {rule_id} not found"}
        await db.execute("DELETE FROM classification_rules WHERE id = ?", (int(rule_id),))
        await db.commit()
        await _write_rules_audit("delete_rule", "deleted_classification", {"id": rule_id})
        await _broadcast_rules_changed("classification")
        return {"status": "deleted", "rule_type": "classification", "id": int(rule_id)}

    elif rule_type == "processing":
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id FROM processing_rules WHERE id = ?", (int(rule_id),)
        )
        if not rows:
            return {"error": f"Processing rule {rule_id} not found"}
        await db.execute("DELETE FROM processing_rules WHERE id = ?", (int(rule_id),))
        await db.commit()
        await _write_rules_audit("delete_rule", "deleted_processing", {"id": rule_id})
        await _broadcast_rules_changed("processing")
        return {"status": "deleted", "rule_type": "processing", "id": int(rule_id)}

    else:
        return {"error": f"Unknown rule_type: {rule_type}. Must be 'filter', 'classification', or 'processing'"}
