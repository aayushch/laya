"""Daily briefing pipeline — generate a briefing action card."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.briefing import build_briefing_messages
from laya.models.card import ActionCardData, StagedOutput, SuggestedAction
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.emit import run_emit

log = structlog.get_logger()


async def generate_briefing() -> str:
    """Generate a daily briefing card.

    Queries overnight events, pending cards, and calendar events,
    then synthesizes a briefing via LLM and creates an action card.

    Returns:
        card_id of the created briefing card.
    """
    settings = load_settings()
    db = await get_db()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # 1. Query overnight events (last 12 hours)
    overnight_rows = await db.execute_fetchall(
        """SELECT event_id, source_platform, subject_title, actor_name, timestamp
           FROM events
           WHERE timestamp > datetime('now', '-12 hours')
             AND filtered = 0
           ORDER BY timestamp DESC
           LIMIT 30"""
    )
    overnight_events = [
        {
            "event_id": row[0],
            "source_platform": row[1],
            "subject_title": row[2],
            "actor_name": row[3],
            "timestamp": row[4],
        }
        for row in overnight_rows
    ]

    # 2. Query pending action cards
    pending_rows = await db.execute_fetchall(
        """SELECT card_id, header, summary, priority, persona
           FROM action_cards
           WHERE status IN ('pending', 'ready')
           ORDER BY CASE priority
               WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
               WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 END ASC
           LIMIT 10"""
    )
    pending_cards = [
        {
            "card_id": row[0],
            "header": row[1],
            "summary": row[2],
            "priority": row[3],
            "persona": row[4],
        }
        for row in pending_rows
    ]

    # 3. Query today's calendar events
    calendar_rows = await db.execute_fetchall(
        """SELECT event_id, subject_title, timestamp
           FROM events
           WHERE source_platform = 'calendar'
             AND DATE(timestamp) = DATE('now')
           ORDER BY timestamp ASC
           LIMIT 10"""
    )
    calendar_events = [
        {
            "event_id": row[0],
            "subject_title": row[1],
            "timestamp": row[2],
        }
        for row in calendar_rows
    ]

    # 4. Quick stats
    stats_rows = await db.execute_fetchall(
        """SELECT
               (SELECT COUNT(*) FROM events WHERE timestamp > datetime('now', '-24 hours') AND processed = 1) as processed,
               (SELECT COUNT(*) FROM action_cards WHERE created_at > datetime('now', '-24 hours')) as generated,
               (SELECT COUNT(*) FROM action_cards WHERE resolved_at > datetime('now', '-24 hours')) as resolved"""
    )
    stats = {
        "events_processed": stats_rows[0][0] or 0,
        "cards_generated": stats_rows[0][1] or 0,
        "cards_resolved": stats_rows[0][2] or 0,
    }

    # 5. Generate briefing via LLM
    messages = build_briefing_messages(
        overnight_events, pending_cards, calendar_events, stats
    )

    try:
        response = await llm_call(
            role="stager",
            messages=messages,
            step="briefing",
            temperature=0.3,
            max_tokens=2000,
        )
        briefing_text = response.content
    except Exception as e:
        log.error("briefing_llm_failed", error=str(e))
        briefing_text = _build_fallback_briefing(
            overnight_events, pending_cards, stats
        )

    # 6. Create synthetic event
    briefing_event_id = f"evt_briefing_{today}"

    # Check if briefing event already exists today
    existing = await db.execute_fetchall(
        "SELECT event_id FROM events WHERE event_id = ?",
        (briefing_event_id,),
    )
    if existing:
        log.info("briefing_already_exists", date=today)
        # Return existing card
        card_rows = await db.execute_fetchall(
            "SELECT card_id FROM action_cards WHERE event_id = ?",
            (briefing_event_id,),
        )
        return card_rows[0][0] if card_rows else ""

    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            actor_name, actor_email, subject_type, subject_id, subject_title,
            content_body, raw_json, processed, processing_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            briefing_event_id,
            now.isoformat(),
            "laya",
            "daily_briefing",
            "Laya",
            "laya@local",
            "briefing",
            f"briefing_{today}",
            f"Daily Briefing - {today}",
            briefing_text,
            "{}",
            True,
            "completed",
        ),
    )
    await db.commit()

    # 7. Build event model for emit
    briefing_event = LayaEvent(
        event_id=briefing_event_id,
        timestamp=now,
        source={"platform": "laya", "raw_event_type": "daily_briefing"},
        actor={"name": "Laya", "email": "laya@local"},
        subject={
            "type": "briefing",
            "id": f"briefing_{today}",
            "title": f"Daily Briefing - {today}",
        },
        content={"body": briefing_text, "attachments": [], "metadata": {}},
    )

    # Summary line
    summary_parts = []
    if overnight_events:
        summary_parts.append(f"{len(overnight_events)} new events overnight")
    if pending_cards:
        summary_parts.append(f"{len(pending_cards)} cards pending")
    if calendar_events:
        summary_parts.append(f"{len(calendar_events)} calendar events")
    summary = ". ".join(summary_parts) or "No significant activity."

    # Build intelligence bullets
    intelligence = []
    if overnight_events:
        intelligence.append(f"{len(overnight_events)} events processed in the last 12 hours")
    if pending_cards:
        high_count = sum(1 for c in pending_cards if c["priority"] in ("HIGH", "CRITICAL"))
        if high_count:
            intelligence.append(f"{high_count} high-priority cards need attention")
    intelligence.append(f"Stats: {stats['events_processed']} processed, {stats['cards_resolved']} resolved (24h)")

    stager_output = ActionCardData(
        header=f"Daily Briefing - {today}",
        summary=summary,
        intelligence_report=intelligence,
        staged_output=StagedOutput(type="briefing", content=briefing_text),
        suggested_actions=[],
        privacy_tier=1,
    )

    router_output = RouterOutput(
        category="OPS",
        persona="OPS",
        priority="MEDIUM",
        confidence=1.0,
        entities=[],
        research_plan=[],
        requires_research=False,
        secondary_persona=None,
        reasoning="Automated daily briefing",
    )

    # 8. Emit the card
    card_id = await run_emit(briefing_event, router_output, stager_output)

    # Broadcast briefing_ready
    await manager.broadcast({
        "type": "briefing_ready",
        "card_id": card_id,
        "payload": {"header": stager_output.header},
    })

    log.info("briefing_generated", card_id=card_id, date=today)
    return card_id


def _build_fallback_briefing(
    overnight_events: list[dict],
    pending_cards: list[dict],
    stats: dict,
) -> str:
    """Build a simple text briefing when LLM fails."""
    lines = [f"# Daily Briefing\n"]

    lines.append("## Overnight Activity")
    if overnight_events:
        for evt in overnight_events[:10]:
            lines.append(f"- [{evt['source_platform']}] {evt['subject_title']}")
    else:
        lines.append("No new events overnight.")

    lines.append("\n## Needs Attention")
    if pending_cards:
        for card in pending_cards[:5]:
            lines.append(f"- [{card['priority']}] {card['header']}")
    else:
        lines.append("No pending cards.")

    lines.append(f"\n## Stats (24h)")
    lines.append(f"- Events: {stats.get('events_processed', 0)}")
    lines.append(f"- Cards: {stats.get('cards_generated', 0)}")
    lines.append(f"- Resolved: {stats.get('cards_resolved', 0)}")

    return "\n".join(lines)
