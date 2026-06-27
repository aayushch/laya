# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Usage-limit budgeting for the agent inference backend.

Agents (Claude Code, Codex, …) don't bill in dollars — they bill against time-windowed
usage limits that reset (Claude: a rolling 5-hour window). So this is a parallel to the
monthly $ budget (``pipeline/budget.py``) but **window-based with auto-resume**:

- Per-agent config (``settings.agent_budgets.agents[agent_id]``): a token budget over a
  rolling window. We sum the agent's in+out tokens from ``audit_log`` (where agent calls
  are logged as ``model_used='agent/<id>/…'``) over the last ``window_hours``.
- Native signal (Claude Code only): we scrape ``rate_limit_event.rate_limit_info`` from the
  CLI (status + resetsAt) and treat it as authoritative when present.
- When an agent crosses ``pause_at_percent`` of its window budget (or its native limit is
  hit), we pause ingestion (same n8n lever the $ budget uses) until the window resets, then
  the scheduler auto-resumes. This is the user-chosen "defer + auto-resume at reset".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog

from laya.config import load_settings, save_settings
from laya.db.sqlite import get_db

log = structlog.get_logger()

DEFAULT_PAUSE_AT_PERCENT = 85
DEFAULT_WINDOW_HOURS = 5.0

# Native rate-limit statuses we consider "still OK to use".
_OK_STATUSES = {"allowed", None, ""}


# ── config ───────────────────────────────────────────────────────────────


def get_agent_budget_config() -> dict:
    """Read agent-budget config from settings.json."""
    s = load_settings().get("agent_budgets", {}) or {}
    return {"enabled": bool(s.get("enabled", False)), "agents": s.get("agents", {}) or {}}


def update_agent_budget_config(enabled: bool, agents: dict | None) -> dict:
    """Persist agent-budget config to settings.json."""
    s = load_settings()
    ab = s.get("agent_budgets", {}) or {}
    ab["enabled"] = bool(enabled)
    if agents is not None:
        ab["agents"] = agents
    s["agent_budgets"] = ab
    save_settings(s)
    return get_agent_budget_config()


# ── time helpers ─────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _audit_window_start(window_hours: float) -> str:
    """UTC 'YYYY-MM-DD HH:MM:SS' for the start of the rolling window (matches audit_log)."""
    return (_now_utc() - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _iso_in_hours(hours: float) -> str:
    return (_now_utc() + timedelta(hours=hours)).isoformat()


def _iso_from_unix(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


# ── rate-limit signal (Claude Code) ──────────────────────────────────────


async def record_rate_limit(agent_id: str, info: dict) -> None:
    """Upsert the latest native rate-limit signal for an agent. Never raises."""
    if not info:
        return
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO agent_rate_limit_state
                   (agent_id, status, resets_at, rate_limit_type, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(agent_id) DO UPDATE SET
                   status=excluded.status, resets_at=excluded.resets_at,
                   rate_limit_type=excluded.rate_limit_type, raw_json=excluded.raw_json,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                agent_id,
                info.get("status"),
                info.get("resetsAt"),
                info.get("rateLimitType"),
                json.dumps(info),
            ),
        )
        await db.commit()
    except Exception as e:
        log.warning("agent_rate_limit_record_failed", agent_id=agent_id, error=str(e))


async def get_rate_limit_state(agent_id: str) -> dict | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT status, resets_at, rate_limit_type FROM agent_rate_limit_state WHERE agent_id = ?",
        (agent_id,),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "status": r["status"],
        "resets_at": r["resets_at"],
        "rate_limit_type": r["rate_limit_type"],
    }


# ── usage accounting ─────────────────────────────────────────────────────


async def get_agent_window_usage(agent_id: str, window_hours: float) -> dict:
    """Tokens (in+out) consumed by an agent in the rolling window, from audit_log."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS toks,
                  COUNT(*) AS n
           FROM audit_log
           WHERE (model_used = ? OR model_used LIKE ?)
             AND timestamp > ? AND success = 1""",
        (f"agent/{agent_id}", f"agent/{agent_id}/%", _audit_window_start(window_hours)),
    )
    row = rows[0] if rows else None
    return {"tokens": (row["toks"] if row else 0) or 0, "calls": (row["n"] if row else 0) or 0}


# ── pause state ──────────────────────────────────────────────────────────


async def _get_state() -> dict:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT paused_at, paused_until, paused_reason FROM agent_budget_state WHERE id = 1"
    )
    if not rows:
        return {"paused_at": None, "paused_until": None, "paused_reason": None}
    r = rows[0]
    return {
        "paused_at": r["paused_at"],
        "paused_until": r["paused_until"],
        "paused_reason": r["paused_reason"],
    }


async def is_agent_budget_paused() -> bool:
    return (await _get_state())["paused_at"] is not None


async def get_paused_workflow_count() -> int:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT COUNT(*) AS cnt FROM agent_budget_paused_workflows")
    return rows[0]["cnt"] if rows else 0


# ── status (for API / settings panel / footer) ───────────────────────────


async def get_agent_budget_status() -> dict:
    """Per-agent window usage + limits + native rate-limit + pause state."""
    cfg = get_agent_budget_config()
    state = await _get_state()

    agents_status = []
    for agent_id, ac in cfg["agents"].items():
        window_hours = float(ac.get("window_hours") or DEFAULT_WINDOW_HOURS)
        limit = int(ac.get("window_token_limit") or 0)
        usage = await get_agent_window_usage(agent_id, window_hours)
        rl = await get_rate_limit_state(agent_id)
        percent = round(100.0 * usage["tokens"] / limit, 1) if limit > 0 else None
        agents_status.append({
            "agent_id": agent_id,
            "window_hours": window_hours,
            "window_token_limit": limit,
            "pause_at_percent": int(ac.get("pause_at_percent") or DEFAULT_PAUSE_AT_PERCENT),
            "tokens_used": usage["tokens"],
            "calls": usage["calls"],
            "percent": percent,
            "rate_limit": rl,
        })

    return {
        "enabled": cfg["enabled"],
        "agents": agents_status,
        "is_paused": state["paused_at"] is not None,
        "paused_until": state["paused_until"],
        "paused_reason": state["paused_reason"],
        "paused_workflow_count": await get_paused_workflow_count() if state["paused_at"] else 0,
    }


# ── evaluate / pause / resume ─────────────────────────────────────────────


async def evaluate_agent_budget() -> None:
    """Pause ingestion if any configured agent is over its window/native limit.

    Designed to be called fire-and-forget after an agent llm_call (like check_budget).
    """
    cfg = get_agent_budget_config()
    if not cfg["enabled"] or not cfg["agents"]:
        return
    if (await _get_state())["paused_at"] is not None:
        return  # already paused; the scheduler handles resume

    recovery_times: list[str] = []
    reasons: list[str] = []
    for agent_id, ac in cfg["agents"].items():
        window_hours = float(ac.get("window_hours") or DEFAULT_WINDOW_HOURS)
        limit = int(ac.get("window_token_limit") or 0)
        pause_pct = int(ac.get("pause_at_percent") or DEFAULT_PAUSE_AT_PERCENT)
        rl = await get_rate_limit_state(agent_id)

        # Native signal first — when the CLI itself reports we're no longer allowed.
        if rl and rl.get("status") not in _OK_STATUSES:
            recovery_times.append(_iso_from_unix(rl.get("resets_at")) or _iso_in_hours(window_hours))
            reasons.append(f"{agent_id}: rate limit {rl['status']}")
            continue

        # Token-window budget.
        if limit > 0:
            usage = await get_agent_window_usage(agent_id, window_hours)
            if usage["tokens"] >= (pause_pct / 100.0) * limit:
                # Prefer the native reset time when known; else a full window from now
                # (by which point the rolling window has fully turned over).
                recovery_times.append(
                    _iso_from_unix(rl.get("resets_at")) if rl and rl.get("resets_at")
                    else _iso_in_hours(window_hours)
                )
                reasons.append(f"{agent_id}: {usage['tokens']}/{limit} tokens in {window_hours}h")

    if recovery_times:
        # Pause until the latest offender recovers, so a single resume clears all of them.
        await pause_for_agent_budget(max(recovery_times), "; ".join(reasons))


async def pause_for_agent_budget(paused_until: str, reason: str) -> dict:
    """Deactivate all ingestion workflows until an agent's usage window resets."""
    from laya.integrations.n8n_client import activate_workflow, N8nApiError, N8nApiKeyMissing

    db = await get_db()
    if await is_agent_budget_paused():
        return {"already_paused": True}

    source_rows = await db.execute_fetchall(
        "SELECT source_id, workflow_id, space_id FROM sources WHERE source_type = 'ingestion'"
    )
    paused, errors = [], []
    for sr in source_rows:
        wf_id = sr["workflow_id"]
        try:
            await activate_workflow(wf_id, active=False)
            await db.execute(
                "INSERT OR IGNORE INTO agent_budget_paused_workflows (workflow_id, source_id, space_id) VALUES (?, ?, ?)",
                (wf_id, sr["source_id"], sr["space_id"]),
            )
            paused.append(wf_id)
        except (N8nApiError, N8nApiKeyMissing) as e:
            errors.append({"workflow_id": wf_id, "error": str(e)})

    await db.execute(
        "UPDATE agent_budget_state SET paused_at = CURRENT_TIMESTAMP, paused_until = ?, paused_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
        (paused_until, reason),
    )
    await db.commit()
    log.warning("agent_budget_paused", reason=reason, paused_until=paused_until, paused_count=len(paused))
    await _broadcast_status(paused=True)
    return {"paused_count": len(paused), "errors": errors}


async def resume_from_agent_budget() -> dict:
    """Reactivate workflows paused by an agent-usage pause and clear the pause state."""
    from laya.integrations.n8n_client import (
        activate_workflow,
        check_workflow_readiness,
        N8nApiError,
        N8nApiKeyMissing,
    )

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT workflow_id, source_id, space_id FROM agent_budget_paused_workflows"
    )

    resumed, errors = [], []
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

    await db.execute("DELETE FROM agent_budget_paused_workflows")
    await db.execute(
        "UPDATE agent_budget_state SET paused_at = NULL, paused_until = NULL, paused_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
    )
    await db.commit()
    log.info("agent_budget_resumed", resumed_count=len(resumed), errors=len(errors))
    await _broadcast_status(paused=False)
    return {"resumed_count": len(resumed), "errors": errors}


async def maybe_resume_agent_budget() -> None:
    """Scheduler tick: auto-resume once the agent's usage window has reset."""
    state = await _get_state()
    if not state["paused_at"]:
        return
    paused_until = state["paused_until"]
    if paused_until:
        try:
            pu = datetime.fromisoformat(paused_until)
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
        except ValueError:
            pu = _now_utc()  # malformed → eligible to resume
        if _now_utc() < pu:
            return  # window hasn't reset yet
    log.info("agent_budget_auto_resume", reason="window_reset")
    await resume_from_agent_budget()


async def _broadcast_status(paused: bool) -> None:
    try:
        from laya.api.websocket import manager
        await manager.broadcast({"type": "agent_budget_status", "paused": paused})
    except Exception as e:
        log.warning("agent_budget_broadcast_failed", error=str(e))
