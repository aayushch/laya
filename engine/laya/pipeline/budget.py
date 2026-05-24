# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Budget enforcement — monthly cost limits with automatic workflow pause/resume."""

from __future__ import annotations

import asyncio
from datetime import datetime

import structlog

from laya.db.sqlite import get_db
from laya.tz import safe_zoneinfo

log = structlog.get_logger()

# Re-use the pricing table from dashboard so costs are consistent.
from laya.api.dashboard_api import MODEL_PRICING  # noqa: E402

_DEFAULT_PRICING = {"input": 1.0, "output": 3.0}

# Maps audit_log step values to high-level features for cost breakdown.
STEP_TO_FEATURE: dict[str, str] = {
    "route": "Pulse",
    "stage": "Pulse",
    "emit": "Pulse",
    "entity_confirm": "Pulse",
    "context_confirm": "Pulse",
    "context_learn": "Pulse",
    "learn": "Pulse",
    "summarize": "Pulse",
    "worker": "Pulse",
    "trace": "Coherence",
    "trace_filter": "Coherence",
    "trace_summary": "Coherence",
    "omni_resynthesis": "Omni",
    "chat": "Chat",
    "briefing": "Briefing",
    "egress_draft": "Egress",
    "execute": "Egress",
    "lifecycle": "System",
    "recovery": "System",
}


def _current_year_month_local(tz_name: str = "America/New_York") -> str:
    """Return 'YYYY-MM' in the user's configured timezone."""
    return datetime.now(safe_zoneinfo(tz_name)).strftime("%Y-%m")


def _user_timezone() -> str:
    from laya.config import load_settings
    settings = load_settings()
    return settings.get("briefing", {}).get("timezone", "America/New_York")


async def get_budget_config() -> dict:
    """Return the singleton budget_config row."""
    db = await get_db()
    async with db.execute("SELECT * FROM budget_config WHERE id = 1") as cursor:
        row = await cursor.fetchone()
    if not row:
        return {"monthly_limit_usd": None, "enabled": False, "paused_at": None}
    return {
        "monthly_limit_usd": row["monthly_limit_usd"],
        "enabled": bool(row["enabled"]),
        "paused_at": row["paused_at"],
    }


async def update_budget_config(monthly_limit_usd: float | None, enabled: bool) -> dict:
    """Update budget configuration."""
    db = await get_db()
    await db.execute(
        """UPDATE budget_config
           SET monthly_limit_usd = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = 1""",
        (monthly_limit_usd, 1 if enabled else 0),
    )
    await db.commit()
    return await get_budget_config()


async def get_current_month_cost(tz_name: str | None = None) -> dict:
    """Compute current calendar-month cost from audit_log (live query).

    Returns dict with total_cost_usd, by_model, total_input_tokens, total_output_tokens, year_month.
    Uses LOCAL timezone for month boundary.
    """
    tz = tz_name or _user_timezone()
    year_month = _current_year_month_local(tz)
    # Convert local month start to UTC for querying audit_log (stored in UTC)
    local_tz = safe_zoneinfo(tz)

    month_start_local = datetime.strptime(f"{year_month}-01", "%Y-%m-%d").replace(tzinfo=local_tz)
    month_start_utc = month_start_local.astimezone(safe_zoneinfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT model_used, step,
                  SUM(input_tokens) as total_in,
                  SUM(output_tokens) as total_out
           FROM audit_log
           WHERE timestamp > ? AND success = 1
           GROUP BY model_used, step""",
        (month_start_utc,),
    )

    total_cost = 0.0
    by_model: dict[str, float] = {}
    by_step: dict[str, float] = {}
    by_feature: dict[str, float] = {}
    total_in = 0
    total_out = 0

    for row in rows:
        model = row[0] or "unknown"
        step = row[1] or "unknown"
        in_tokens = row[2] or 0
        out_tokens = row[3] or 0
        total_in += in_tokens
        total_out += out_tokens
        pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
        cost = (in_tokens * pricing["input"] + out_tokens * pricing["output"]) / 1_000_000

        by_model[model] = round(by_model.get(model, 0.0) + cost, 4)
        by_step[step] = round(by_step.get(step, 0.0) + cost, 4)
        feature = STEP_TO_FEATURE.get(step, "Other")
        by_feature[feature] = round(by_feature.get(feature, 0.0) + cost, 4)
        total_cost += cost

    return {
        "year_month": year_month,
        "total_cost_usd": round(total_cost, 4),
        "by_model": by_model,
        "by_feature": by_feature,
        "by_step": by_step,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }


async def get_paused_workflow_count() -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) as cnt FROM budget_paused_workflows") as cursor:
        row = await cursor.fetchone()
    return row["cnt"] if row else 0


async def is_budget_paused() -> bool:
    """True if workflows are currently paused due to budget."""
    cfg = await get_budget_config()
    return cfg["paused_at"] is not None


async def pause_for_budget() -> dict:
    """Pause all active ingestion workflows across all spaces due to budget.

    Records which workflows were paused so only those are resumed later.
    Broadcasts a WebSocket event to notify the UI.
    """
    from laya.integrations.n8n_client import activate_workflow, N8nApiError, N8nApiKeyMissing

    db = await get_db()

    # Check if already paused
    if await is_budget_paused():
        count = await get_paused_workflow_count()
        log.info("budget_already_paused", paused_workflows=count)
        return {"already_paused": True, "paused_count": count}

    # Get all ingestion workflows across all spaces
    source_rows = await db.execute_fetchall(
        "SELECT source_id, workflow_id, space_id FROM sources WHERE source_type = 'ingestion'"
    )

    paused = []
    errors = []
    for sr in source_rows:
        wf_id = sr["workflow_id"]
        try:
            await activate_workflow(wf_id, active=False)
            await db.execute(
                "INSERT OR IGNORE INTO budget_paused_workflows (workflow_id, source_id, space_id) VALUES (?, ?, ?)",
                (wf_id, sr["source_id"], sr["space_id"]),
            )
            paused.append(wf_id)
        except (N8nApiError, N8nApiKeyMissing) as e:
            errors.append({"workflow_id": wf_id, "error": str(e)})

    # Mark budget as paused
    await db.execute(
        "UPDATE budget_config SET paused_at = CURRENT_TIMESTAMP WHERE id = 1"
    )
    await db.commit()

    log.warning("budget_limit_reached", paused_count=len(paused), errors=len(errors))

    # Broadcast to UI
    await _broadcast_budget_status(paused=True)

    return {"paused_count": len(paused), "errors": errors}


async def resume_from_budget() -> dict:
    """Resume workflows that were paused by budget enforcement.

    Only resumes workflows recorded in budget_paused_workflows — doesn't
    touch manually-deactivated workflows.
    """
    from laya.integrations.n8n_client import (
        activate_workflow,
        check_workflow_readiness,
        N8nApiError,
        N8nApiKeyMissing,
    )

    db = await get_db()
    rows = await db.execute_fetchall("SELECT workflow_id, source_id, space_id FROM budget_paused_workflows")

    if not rows:
        # Clear paused state even if no workflows to resume
        await db.execute("UPDATE budget_config SET paused_at = NULL WHERE id = 1")
        await db.commit()
        await _broadcast_budget_status(paused=False)
        return {"resumed_count": 0, "errors": []}

    resumed = []
    errors = []
    for row in rows:
        wf_id = row["workflow_id"]
        try:
            readiness = await check_workflow_readiness(wf_id)
            if not readiness["ready"]:
                errors.append({"workflow_id": wf_id, "issues": readiness["issues"]})
                continue
            await activate_workflow(wf_id, active=True)
            resumed.append(wf_id)
        except (N8nApiError, N8nApiKeyMissing) as e:
            errors.append({"workflow_id": wf_id, "error": str(e)})

    # Clear paused state and tracking table
    await db.execute("DELETE FROM budget_paused_workflows")
    await db.execute("UPDATE budget_config SET paused_at = NULL WHERE id = 1")
    await db.commit()

    log.info("budget_workflows_resumed", resumed_count=len(resumed), errors=len(errors))

    await _broadcast_budget_status(paused=False)

    return {"resumed_count": len(resumed), "errors": errors}


async def snapshot_month(year_month: str) -> None:
    """Aggregate audit_log for a given month and persist to monthly_costs.

    Should be called once when rolling over to a new month.
    """
    tz = _user_timezone()
    local_tz = safe_zoneinfo(tz)

    # Compute UTC boundaries for the local month
    month_start_local = datetime.strptime(f"{year_month}-01", "%Y-%m-%d").replace(tzinfo=local_tz)

    # Next month start
    y, m = int(year_month[:4]), int(year_month[5:7])
    if m == 12:
        next_start_local = month_start_local.replace(year=y + 1, month=1)
    else:
        next_start_local = month_start_local.replace(month=m + 1)

    start_utc = month_start_local.astimezone(safe_zoneinfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = next_start_local.astimezone(safe_zoneinfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT model_used,
                  SUM(input_tokens) as total_in,
                  SUM(output_tokens) as total_out
           FROM audit_log
           WHERE timestamp >= ? AND timestamp < ? AND success = 1
           GROUP BY model_used""",
        (start_utc, end_utc),
    )

    for row in rows:
        model = row[0] or "unknown"
        in_tokens = row[1] or 0
        out_tokens = row[2] or 0
        pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
        cost = (in_tokens * pricing["input"] + out_tokens * pricing["output"]) / 1_000_000

        await db.execute(
            """INSERT INTO monthly_costs (year_month, model_used, input_tokens, output_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(year_month, model_used)
               DO UPDATE SET input_tokens = excluded.input_tokens,
                             output_tokens = excluded.output_tokens,
                             cost_usd = excluded.cost_usd""",
            (year_month, model, in_tokens, out_tokens, round(cost, 4)),
        )

    await db.commit()
    log.info("monthly_cost_snapshot_saved", year_month=year_month, models=len(rows))


async def get_monthly_history(limit: int = 12) -> list[dict]:
    """Return monthly cost history (most recent first)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT year_month, SUM(cost_usd) as total, SUM(input_tokens) as total_in, SUM(output_tokens) as total_out
           FROM monthly_costs
           GROUP BY year_month
           ORDER BY year_month DESC
           LIMIT ?""",
        (limit,),
    )
    result = []
    for row in rows:
        # Get per-model breakdown for this month
        model_rows = await db.execute_fetchall(
            "SELECT model_used, cost_usd FROM monthly_costs WHERE year_month = ?",
            (row["year_month"],),
        )
        by_model = {mr["model_used"]: mr["cost_usd"] for mr in model_rows}
        result.append({
            "year_month": row["year_month"],
            "total_cost_usd": round(row["total"], 4),
            "by_model": by_model,
            "total_input_tokens": row["total_in"] or 0,
            "total_output_tokens": row["total_out"] or 0,
        })
    return result


async def check_budget() -> None:
    """Check if current month cost exceeds budget. If so, pause workflows.

    This is designed to be called after every llm_call audit log write.
    It's lightweight: one SELECT on budget_config, and only if enabled does it
    compute the current month cost.
    """
    db = await get_db()
    async with db.execute(
        "SELECT monthly_limit_usd, enabled, paused_at FROM budget_config WHERE id = 1"
    ) as cursor:
        cfg_row = await cursor.fetchone()
    if not cfg_row or not cfg_row["enabled"] or cfg_row["monthly_limit_usd"] is None:
        return
    if cfg_row["paused_at"] is not None:
        return  # Already paused

    cost_data = await get_current_month_cost()
    if cost_data["total_cost_usd"] >= cfg_row["monthly_limit_usd"]:
        log.warning(
            "budget_exceeded",
            current_cost=cost_data["total_cost_usd"],
            limit=cfg_row["monthly_limit_usd"],
            month=cost_data["year_month"],
        )
        await pause_for_budget()


async def on_month_rollover(previous_month: str) -> None:
    """Handle month boundary crossing.

    1. Snapshot the previous month's costs into monthly_costs.
    2. If budget pause was active, auto-resume (new month = fresh budget).
    """
    log.info("month_rollover_detected", previous_month=previous_month)

    # Snapshot previous month
    await snapshot_month(previous_month)

    # If paused, resume — new month means cost is back to 0
    if await is_budget_paused():
        log.info("budget_auto_resume_on_new_month")
        await resume_from_budget()


async def _broadcast_budget_status(paused: bool) -> None:
    """Notify all connected WebSocket clients about budget pause status."""
    from laya.api.websocket import manager
    await manager.broadcast({
        "type": "budget_status",
        "paused": paused,
    })
