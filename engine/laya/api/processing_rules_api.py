"""API router for processing rules CRUD and testing."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException

from laya.db.sqlite import get_db
from laya.models.processing_rules import (
    CreateProcessingRuleRequest,
    ProcessingRule,
    ProcessingSimpleCondition,
    UpdateProcessingRuleRequest,
)
from laya.pipeline.processing_rules import (
    _parse_actions,
    _parse_condition,
    build_trigger_context,
    evaluate_condition,
)

_MAX_REGEX_LEN = 500

log = structlog.get_logger()
router = APIRouter()


def _row_to_rule(row: dict) -> dict:
    """Convert a DB row to a ProcessingRule dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "space_id": row["space_id"],
        "enabled": bool(row["enabled"]),
        "position": row["position"],
        "condition": json.loads(row["condition_json"]),
        "actions": json.loads(row["actions_json"]),
        "rate_limit": row["rate_limit"],
        "cooldown_secs": row["cooldown_secs"],
        "max_daily": row["max_daily"],
        "last_fired_at": row["last_fired_at"],
        "fire_count": row["fire_count"],
        "error_count": row["error_count"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/processing-rules/settings")
async def get_processing_rules_settings() -> dict:
    """Return processing-rules engine settings (e.g. auto-disable threshold)."""
    from laya.config import load_settings
    settings = load_settings()
    pr_settings = settings.get("processing_rules", {})
    return {
        "auto_disable_threshold": pr_settings.get("auto_disable_threshold", 5),
    }


@router.put("/processing-rules/settings")
async def update_processing_rules_settings(body: dict) -> dict:
    """Update processing-rules engine settings."""
    from laya.config import load_settings, save_settings
    settings = load_settings()
    if "processing_rules" not in settings:
        settings["processing_rules"] = {}
    if "auto_disable_threshold" in body:
        val = int(body["auto_disable_threshold"])
        if val < 1:
            val = 1
        if val > 100:
            val = 100
        settings["processing_rules"]["auto_disable_threshold"] = val
    save_settings(settings)
    return {"auto_disable_threshold": settings["processing_rules"]["auto_disable_threshold"]}


@router.get("/processing-rules")
async def list_processing_rules(space_id: str | None = None) -> dict:
    """List all processing rules, optionally filtered by space."""
    db = await get_db()
    if space_id:
        rows = await db.execute_fetchall(
            "SELECT * FROM processing_rules WHERE space_id IS NULL OR space_id = ? ORDER BY position ASC",
            (space_id,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM processing_rules ORDER BY position ASC",
        )
    return {"rules": [_row_to_rule(r) for r in rows]}


def _validate_regex_patterns(condition) -> None:
    """Validate regex patterns in conditions at creation time."""
    if isinstance(condition, ProcessingSimpleCondition):
        if condition.operator.value == "matches" and condition.value:
            pattern = str(condition.value)
            if len(pattern) > _MAX_REGEX_LEN:
                raise HTTPException(status_code=422, detail=f"Regex pattern too long ({len(pattern)} > {_MAX_REGEX_LEN})")
            try:
                re.compile(pattern)
            except re.error as e:
                raise HTTPException(status_code=422, detail=f"Invalid regex pattern: {e}")
    elif hasattr(condition, "all"):
        for c in condition.all:
            _validate_regex_patterns(c)
    elif hasattr(condition, "any"):
        for c in condition.any:
            _validate_regex_patterns(c)
    elif hasattr(condition, "not_"):
        _validate_regex_patterns(condition.not_)


@router.post("/processing-rules")
async def create_processing_rule(body: CreateProcessingRuleRequest) -> dict:
    """Create a new processing rule."""
    _validate_regex_patterns(body.condition)

    # Validate condition and actions parse correctly
    try:
        condition_json = body.condition.model_dump_json()
        actions_json = json.dumps([a.model_dump() for a in body.actions])
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid rule definition: {e}")

    db = await get_db()

    # Get next position
    rows = await db.execute_fetchall(
        "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM processing_rules"
    )
    next_pos = rows[0]["next_pos"] if rows else 0

    cursor = await db.execute(
        """INSERT INTO processing_rules
           (name, description, space_id, enabled, position, condition_json, actions_json,
            rate_limit, cooldown_secs, max_daily)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.name, body.description, body.space_id, body.enabled,
            next_pos, condition_json, actions_json,
            body.rate_limit, body.cooldown_secs, body.max_daily,
        ),
    )
    await db.commit()

    rule_id = cursor.lastrowid
    log.info("processing_rule_created", rule_id=rule_id, name=body.name)

    rows = await db.execute_fetchall("SELECT * FROM processing_rules WHERE id = ?", (rule_id,))
    return _row_to_rule(rows[0])


@router.get("/processing-rules/field-options")
async def get_field_options() -> dict:
    """Return known values for trigger fields that support dropdown selection."""
    db = await get_db()

    platform_rows = await db.execute_fetchall(
        "SELECT DISTINCT source_platform FROM events WHERE source_platform IS NOT NULL ORDER BY source_platform"
    )
    platforms = [r["source_platform"] for r in platform_rows if r["source_platform"]]

    event_type_rows = await db.execute_fetchall(
        "SELECT DISTINCT source_raw_event_type FROM events WHERE source_raw_event_type IS NOT NULL ORDER BY source_raw_event_type"
    )
    event_types = [r["source_raw_event_type"] for r in event_type_rows if r["source_raw_event_type"]]

    subject_type_rows = await db.execute_fetchall(
        "SELECT DISTINCT subject_type FROM events WHERE subject_type IS NOT NULL ORDER BY subject_type"
    )
    subject_types = [r["subject_type"] for r in subject_type_rows if r["subject_type"]]

    return {
        "event.source.platform": platforms or ["jira", "github", "slack", "gmail", "bitbucket", "calendar", "linear", "outlook", "notion"],
        "event.source.raw_event_type": event_types,
        "event.subject.type": subject_types,
        "classification.persona": ["ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"],
        "classification.priority": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "classification.category": ["CODE", "COMMS", "PEOPLE", "FINANCE", "OPS"],
        "context.actor_relationship": ["self", "team", "external", "bot"],
    }


@router.get("/processing-rules/{rule_id}")
async def get_processing_rule(rule_id: int) -> dict:
    """Get a single processing rule."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM processing_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _row_to_rule(rows[0])


@router.put("/processing-rules/{rule_id}")
async def update_processing_rule(rule_id: int, body: UpdateProcessingRuleRequest) -> dict:
    """Update a processing rule."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM processing_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.condition is not None:
        _validate_regex_patterns(body.condition)

    updates = []
    params = []

    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)
    if body.space_id is not None:
        updates.append("space_id = ?")
        params.append(body.space_id)
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(body.enabled)
        if body.enabled:
            updates.append("error_count = 0")
    if body.condition is not None:
        updates.append("condition_json = ?")
        params.append(body.condition.model_dump_json())
    if body.actions is not None:
        updates.append("actions_json = ?")
        params.append(json.dumps([a.model_dump() for a in body.actions]))
    if body.rate_limit is not None:
        updates.append("rate_limit = ?")
        params.append(body.rate_limit)
    if body.cooldown_secs is not None:
        updates.append("cooldown_secs = ?")
        params.append(body.cooldown_secs)
    if body.max_daily is not None:
        updates.append("max_daily = ?")
        params.append(body.max_daily)

    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(rule_id)
        await db.execute(
            f"UPDATE processing_rules SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()

    rows = await db.execute_fetchall("SELECT * FROM processing_rules WHERE id = ?", (rule_id,))
    return _row_to_rule(rows[0])


@router.delete("/processing-rules/{rule_id}")
async def delete_processing_rule(rule_id: int) -> dict:
    """Delete a processing rule and its firing history."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM processing_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.execute("DELETE FROM processing_rules WHERE id = ?", (rule_id,))
    await db.commit()
    log.info("processing_rule_deleted", rule_id=rule_id)
    return {"status": "deleted", "id": rule_id}


@router.put("/processing-rules/{rule_id}/toggle")
async def toggle_processing_rule(rule_id: int) -> dict:
    """Toggle a rule's enabled state."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT enabled FROM processing_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    new_enabled = not bool(rows[0]["enabled"])
    updates = "enabled = ?, updated_at = CURRENT_TIMESTAMP"
    params: list = [new_enabled]
    if new_enabled:
        updates += ", error_count = 0"
    params.append(rule_id)

    await db.execute(f"UPDATE processing_rules SET {updates} WHERE id = ?", params)
    await db.commit()
    return {"id": rule_id, "enabled": new_enabled}


@router.put("/processing-rules/reorder")
async def reorder_processing_rules(body: dict) -> dict:
    """Bulk update rule positions. Body: {"order": [rule_id, rule_id, ...]}"""
    order = body.get("order", [])
    if not order:
        raise HTTPException(status_code=422, detail="Missing 'order' list")

    db = await get_db()
    for pos, rid in enumerate(order):
        await db.execute(
            "UPDATE processing_rules SET position = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pos, rid),
        )
    await db.commit()
    return {"status": "reordered", "count": len(order)}


@router.post("/processing-rules/preview-matches")
async def preview_matches(body: dict) -> dict:
    """Count recent cards matching a condition. Body: {"condition": {...}}"""
    condition_data = body.get("condition")
    if not condition_data:
        raise HTTPException(status_code=422, detail="Missing 'condition'")

    try:
        condition = _parse_condition(condition_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid condition: {e}")

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.header, ac.priority, ac.persona, ac.category,
                  ac.entity_id, ac.space_id, ac.confidence, ac.status,
                  e.raw_json
           FROM action_cards ac
           LEFT JOIN events e ON e.event_id = ac.event_id
           WHERE ac.created_at > datetime('now', '-7 days')
           ORDER BY ac.created_at DESC
           LIMIT 500""",
    )

    from laya.models.classification import RouterOutput, Persona, Priority, Category
    from laya.models.event import LayaEvent

    def _evaluate_rows(rows_data, cond):
        matched = []
        skipped = 0
        for row in rows_data:
            try:
                event_data = json.loads(row["raw_json"]) if row["raw_json"] else None
                if not event_data:
                    skipped += 1
                    continue
                event = LayaEvent(**event_data)
                router_output = RouterOutput(
                    persona=Persona(row["persona"]),
                    priority=Priority(row["priority"]),
                    category=Category(row["category"]),
                    confidence=row["confidence"] or 0.5,
                    entities=[],
                )
                ctx = build_trigger_context(event, router_output, row["card_id"], row["entity_id"], row["space_id"])
                if evaluate_condition(cond, ctx):
                    matched.append({
                        "card_id": row["card_id"],
                        "header": row["header"],
                        "priority": row["priority"],
                        "persona": row["persona"],
                        "status": row["status"],
                    })
            except Exception:
                skipped += 1
                continue
        return matched, skipped

    rows_data = [dict(r) for r in rows]
    matches, skipped = await asyncio.to_thread(_evaluate_rows, rows_data, condition)

    return {
        "match_count": len(matches),
        "sample_cards": matches[:5],
        "period": "last 7 days",
        "scanned": len(rows),
        "skipped": skipped,
    }


@router.get("/processing-rules/{rule_id}/history")
async def get_rule_history(rule_id: int, limit: int = 20) -> dict:
    """Get recent firings for a rule."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM processing_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    firings = await db.execute_fetchall(
        """SELECT id, card_id, entity_id, event_id, fired_at, actions_json, results_json, error
           FROM processing_rule_firings
           WHERE rule_id = ?
           ORDER BY fired_at DESC
           LIMIT ?""",
        (rule_id, limit),
    )
    return {
        "rule_id": rule_id,
        "firings": [
            {
                "id": f["id"],
                "card_id": f["card_id"],
                "entity_id": f["entity_id"],
                "event_id": f["event_id"],
                "fired_at": f["fired_at"],
                "actions": json.loads(f["actions_json"]) if f["actions_json"] else [],
                "results": json.loads(f["results_json"]) if f["results_json"] else [],
                "error": f["error"],
            }
            for f in firings
        ],
    }
