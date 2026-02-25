"""Dashboard REST API — aggregated analytics from SQLite."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from laya.db.sqlite import get_db
from laya.models.dashboard import (
    DashboardResponse,
    DashboardStats,
    LLMCostEstimate,
    PersonaApprovalRate,
    ResponseTimeStats,
    SourceBreakdown,
    TimeSavedEstimate,
)

log = structlog.get_logger()
router = APIRouter()

# Estimated minutes saved per action type (for completed actions)
TIME_ESTIMATES: dict[str, float] = {
    "comment": 3,
    "transition": 1,
    "send_email": 5,
    "merge": 2,
    "approve": 1,
    "code_fix": 15,
    "draft_reply": 5,
    "briefing": 10,
    "summary": 3,
}

# LLM pricing per 1M tokens (input, output) in USD
MODEL_PRICING: dict[str, dict[str, float]] = {
    "anthropic/claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "anthropic/claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "anthropic/claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "google/gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


@router.get("/dashboard")
async def get_dashboard(days: int = 30) -> DashboardResponse:
    """Aggregate dashboard statistics from SQLite."""
    db = await get_db()
    date_filter = f"datetime('now', '-{days} days')"

    # 1. Event counts
    event_rows = await db.execute_fetchall(
        f"""SELECT
                COUNT(*) as total,
                SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as processed,
                SUM(CASE WHEN filtered = 1 THEN 1 ELSE 0 END) as filtered
            FROM events
            WHERE timestamp > {date_filter}"""
    )
    ev = event_rows[0]
    events_processed = ev[1] or 0
    events_filtered = ev[2] or 0

    # 2. Card counts by status
    card_rows = await db.execute_fetchall(
        f"""SELECT status, COUNT(*) as count
            FROM action_cards
            WHERE created_at > {date_filter}
            GROUP BY status"""
    )
    card_counts: dict[str, int] = {}
    for row in card_rows:
        card_counts[row[0]] = row[1]

    cards_generated = sum(card_counts.values())
    cards_edited_rows = await db.execute_fetchall(
        f"""SELECT COUNT(*) FROM action_cards
            WHERE status = 'approved' AND user_feedback IS NOT NULL
              AND created_at > {date_filter}"""
    )
    cards_edited = cards_edited_rows[0][0] or 0

    # 3. Action counts
    action_rows = await db.execute_fetchall(
        f"""SELECT result_status, COUNT(*) as count
            FROM action_log
            WHERE executed_at > {date_filter}
            GROUP BY result_status"""
    )
    action_counts: dict[str, int] = {}
    for row in action_rows:
        action_counts[row[0]] = row[1]

    stats = DashboardStats(
        events_processed=events_processed,
        events_filtered=events_filtered,
        cards_generated=cards_generated,
        cards_pending=card_counts.get("pending", 0),
        cards_approved=card_counts.get("approved", 0) + card_counts.get("completed", 0),
        cards_dismissed=card_counts.get("dismissed", 0),
        cards_edited=cards_edited,
        actions_executed=sum(action_counts.values()),
        actions_completed=action_counts.get("completed", 0),
        actions_failed=action_counts.get("failed", 0),
    )

    # 4. Time saved
    time_rows = await db.execute_fetchall(
        f"""SELECT action_type, COUNT(*) as count
            FROM action_log
            WHERE result_status = 'completed' AND executed_at > {date_filter}
            GROUP BY action_type"""
    )
    by_action_type: dict[str, float] = {}
    total_minutes = 0.0
    for row in time_rows:
        action_type = row[0]
        count = row[1]
        estimate = TIME_ESTIMATES.get(action_type, 2)
        minutes = estimate * count
        by_action_type[action_type] = minutes
        total_minutes += minutes

    time_saved = TimeSavedEstimate(total_minutes=total_minutes, by_action_type=by_action_type)

    # 5. LLM costs
    cost_rows = await db.execute_fetchall(
        f"""SELECT model_used,
                   SUM(input_tokens) as total_in,
                   SUM(output_tokens) as total_out
            FROM audit_log
            WHERE timestamp > {date_filter} AND success = 1
            GROUP BY model_used"""
    )
    total_cost = 0.0
    by_model: dict[str, float] = {}
    total_input_tokens = 0
    total_output_tokens = 0

    for row in cost_rows:
        model = row[0] or "unknown"
        in_tokens = row[1] or 0
        out_tokens = row[2] or 0
        total_input_tokens += in_tokens
        total_output_tokens += out_tokens

        pricing = MODEL_PRICING.get(model, {"input": 1.0, "output": 3.0})
        cost = (in_tokens * pricing["input"] + out_tokens * pricing["output"]) / 1_000_000
        by_model[model] = round(cost, 4)
        total_cost += cost

    llm_costs = LLMCostEstimate(
        total_cost_usd=round(total_cost, 4),
        by_model=by_model,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
    )

    # 6. Events by source
    source_rows = await db.execute_fetchall(
        f"""SELECT source_platform, COUNT(*) as count
            FROM events
            WHERE timestamp > {date_filter}
            GROUP BY source_platform
            ORDER BY count DESC"""
    )
    events_by_source = [SourceBreakdown(source=row[0], count=row[1]) for row in source_rows]

    # 7. Approval by persona
    persona_rows = await db.execute_fetchall(
        f"""SELECT persona,
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'approved' OR status = 'completed' THEN 1 ELSE 0 END) as approved,
                   SUM(CASE WHEN status = 'dismissed' THEN 1 ELSE 0 END) as dismissed
            FROM action_cards
            WHERE status IN ('approved', 'completed', 'dismissed')
              AND created_at > {date_filter}
            GROUP BY persona"""
    )
    approval_by_persona = []
    for row in persona_rows:
        total = row[1] or 0
        approved = row[2] or 0
        dismissed = row[3] or 0
        rate = approved / total if total > 0 else 0.0
        approval_by_persona.append(
            PersonaApprovalRate(
                persona=row[0],
                total=total,
                approved=approved,
                dismissed=dismissed,
                rate=round(rate, 2),
            )
        )

    # 8. Response time from audit_log (route step)
    rt_rows = await db.execute_fetchall(
        f"""SELECT latency_ms FROM audit_log
            WHERE step = 'route' AND success = 1
              AND timestamp > {date_filter}
            ORDER BY latency_ms ASC"""
    )
    latencies = [row[0] for row in rt_rows if row[0] is not None]
    response_time = ResponseTimeStats()
    if latencies:
        response_time.avg_ms = round(sum(latencies) / len(latencies), 1)
        response_time.p50_ms = float(latencies[len(latencies) // 2])
        p95_idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
        response_time.p95_ms = float(latencies[p95_idx])

    return DashboardResponse(
        stats=stats,
        time_saved=time_saved,
        llm_costs=llm_costs,
        events_by_source=events_by_source,
        approval_by_persona=approval_by_persona,
        response_time=response_time,
        period_days=days,
    )
