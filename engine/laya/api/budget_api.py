"""Budget REST API — monthly cost limits and workflow pause/resume."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from laya.pipeline.budget import (
    check_budget,
    get_budget_config,
    get_current_month_cost,
    get_monthly_history,
    get_paused_workflow_count,
    is_budget_paused,
    resume_from_budget,
    update_budget_config,
)

log = structlog.get_logger()
router = APIRouter()


@router.get("/budget")
async def get_budget() -> dict:
    """Get budget configuration, current month cost, and pause status."""
    config = await get_budget_config()
    cost = await get_current_month_cost()
    paused = await is_budget_paused()
    paused_count = await get_paused_workflow_count() if paused else 0

    return {
        "monthly_limit_usd": config["monthly_limit_usd"],
        "enabled": config["enabled"],
        "current_month_cost": cost["total_cost_usd"],
        "current_month": cost["year_month"],
        "by_model": cost["by_model"],
        "total_input_tokens": cost["total_input_tokens"],
        "total_output_tokens": cost["total_output_tokens"],
        "is_paused": paused,
        "paused_workflow_count": paused_count,
    }


@router.put("/budget")
async def set_budget(body: dict) -> dict:
    """Update budget configuration.

    Body: {"monthly_limit_usd": 10.0, "enabled": true}
    Setting monthly_limit_usd to null disables the limit.
    """
    limit = body.get("monthly_limit_usd")
    enabled = body.get("enabled", False)

    config = await update_budget_config(limit, enabled)
    log.info("budget_config_updated", limit=limit, enabled=enabled)

    # If budget was just enabled, run an immediate check
    if enabled and limit is not None:
        await check_budget()

    return {
        "status": "ok",
        "monthly_limit_usd": config["monthly_limit_usd"],
        "enabled": config["enabled"],
    }


@router.get("/budget/history")
async def get_budget_history(months: int = 12) -> dict:
    """Get monthly cost history."""
    history = await get_monthly_history(limit=months)
    return {"months": history}


@router.post("/budget/resume")
async def manual_resume() -> dict:
    """Manually resume workflows that were paused by budget enforcement."""
    try:
        result = await resume_from_budget()
    except Exception as e:
        log.error("budget_resume_failed", error=str(e), error_type=type(e).__name__)
        raise
    log.info("budget_manual_resume", **result)
    return {
        "status": "resumed",
        "resumed_count": result["resumed_count"],
        "errors": result["errors"],
    }
