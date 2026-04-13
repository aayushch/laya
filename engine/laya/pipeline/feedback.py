"""Learning loop — query user feedback patterns for router prompt injection."""

from __future__ import annotations

import structlog

from laya.db.sqlite import get_db
from laya.models.event import LayaEvent

log = structlog.get_logger()


async def query_feedback_patterns(
    event: LayaEvent,
    limit: int = 10,
) -> list[dict]:
    """Query recent user decisions on similar events.

    Joins action_cards with events to find patterns from the last 30 days,
    grouped by (source_platform, raw_event_type, persona, priority, status).
    Filters to match the incoming event's source platform.

    Returns:
        List of pattern dicts with keys: source_platform, event_type, persona,
        priority, status, count.
    """
    db = await get_db()

    try:
        from laya.config import get_tuning
        window_days = get_tuning("feedback_time_window_days", 30)
        rows = await db.execute_fetchall(
            f"""SELECT
                   e.source_platform,
                   e.source_raw_event_type,
                   ac.persona,
                   ac.priority,
                   ac.status,
                   COUNT(*) as count
               FROM action_cards ac
               JOIN events e ON ac.event_id = e.event_id
               WHERE e.source_platform = ?
                 AND ac.status IN ('done', 'dismissed')
                 AND ac.resolved_at IS NOT NULL
                 AND ac.created_at > datetime('now', '-{window_days} days')
               GROUP BY e.source_platform, e.source_raw_event_type,
                        ac.persona, ac.priority, ac.status
               ORDER BY count DESC
               LIMIT ?""",
            (event.source.platform, limit),
        )
    except Exception as e:
        log.warning("feedback_query_failed", error=str(e))
        return []

    patterns: list[dict] = []
    for row in rows:
        patterns.append({
            "source_platform": row[0],
            "event_type": row[1],
            "persona": row[2],
            "priority": row[3],
            "status": row[4],
            "count": row[5],
        })

    return patterns


async def query_classification_rules(space_id: str | None = None) -> list[dict]:
    """Query active user-defined classification rules."""
    db = await get_db()
    try:
        if space_id:
            rows = await db.execute_fetchall(
                """SELECT field, rule_text FROM classification_rules
                   WHERE active = 1 AND (space_id IS NULL OR space_id = ?)
                   ORDER BY created_at""",
                (space_id,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT field, rule_text FROM classification_rules WHERE active = 1 ORDER BY created_at"
            )
    except Exception as e:
        log.warning("classification_rules_query_failed", error=str(e))
        return []

    return [{"field": r["field"], "rule_text": r["rule_text"]} for r in rows]


async def query_classification_corrections(
    platform: str,
    limit: int = 15,
) -> list[dict]:
    """Query recent user corrections to card classifications."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT field, original_value, corrected_value, card_summary,
                      platform, event_type
               FROM classification_corrections
               WHERE platform = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (platform, limit),
        )
    except Exception as e:
        log.warning("classification_corrections_query_failed", error=str(e))
        return []

    return [
        {
            "field": r["field"],
            "original_value": r["original_value"],
            "corrected_value": r["corrected_value"],
            "card_summary": r["card_summary"],
            "platform": r["platform"],
            "event_type": r["event_type"],
        }
        for r in rows
    ]


def format_feedback_section(
    patterns: list[dict],
    classification_rules: list[dict] | None = None,
    classification_corrections: list[dict] | None = None,
) -> str | None:
    """Format feedback patterns into a prompt section for the router.

    Groups patterns by (platform, event_type) and computes approval/dismissal stats.
    Prepends classification rules and corrections when available.

    Returns:
        Formatted string for prompt injection, or None if nothing to inject.
    """
    sections: list[str] = []

    # 1. Classification rules (explicit user intent — highest priority)
    if classification_rules:
        lines = ["--- USER CLASSIFICATION RULES (always follow these) ---"]
        for i, rule in enumerate(classification_rules, 1):
            prefix = f"[{rule['field']}] " if rule["field"] else ""
            lines.append(f"{i}. {prefix}{rule['rule_text']}")
        lines.append("--- END RULES ---")
        sections.append("\n".join(lines))

    # 2. Recent corrections (implicit signal — learn from the pattern)
    if classification_corrections:
        lines = ["--- RECENT CLASSIFICATION CORRECTIONS (learn from these) ---"]
        for i, c in enumerate(classification_corrections, 1):
            summary = c["card_summary"] or "unknown card"
            ctx = f"{c['platform']}/{c['event_type']}" if c["event_type"] else c["platform"] or ""
            lines.append(
                f"{i}. \"{summary}\" ({ctx}): "
                f"{c['field']} {c['original_value']} → {c['corrected_value']}"
            )
        lines.append("--- END CORRECTIONS ---")
        sections.append("\n".join(lines))

    # 3. Existing feedback patterns (approval/dismissal stats)
    if patterns:
        groups: dict[tuple[str, str], dict] = {}
        for p in patterns:
            key = (p["source_platform"], p["event_type"])
            if key not in groups:
                groups[key] = {"approved": 0, "dismissed": 0, "details": []}
            if p["status"] == "done":
                groups[key]["approved"] += p["count"]
            elif p["status"] == "dismissed":
                groups[key]["dismissed"] += p["count"]
            groups[key]["details"].append(p)

        lines = ["--- USER FEEDBACK PATTERNS (last 30 days) ---"]
        idx = 1
        for (platform, event_type), group in groups.items():
            total = group["approved"] + group["dismissed"]
            if total == 0:
                continue
            rate = group["approved"] / total * 100
            lines.append(
                f"{idx}. For {platform}/{event_type}: "
                f"Approved {group['approved']}/{total}, "
                f"Dismissed {group['dismissed']}/{total} "
                f"({rate:.0f}% approval rate)"
            )

            priority_stats: dict[str, dict] = {}
            for d in group["details"]:
                prio = d["priority"]
                if prio not in priority_stats:
                    priority_stats[prio] = {"approved": 0, "dismissed": 0}
                if d["status"] == "done":
                    priority_stats[prio]["approved"] += d["count"]
                else:
                    priority_stats[prio]["dismissed"] += d["count"]

            if len(priority_stats) > 1:
                for prio, stats in priority_stats.items():
                    ptotal = stats["approved"] + stats["dismissed"]
                    prate = stats["approved"] / ptotal * 100 if ptotal else 0
                    lines.append(
                        f"   - {prio}: {stats['approved']}/{ptotal} approved ({prate:.0f}%)"
                    )

            idx += 1

        lines.append("--- END FEEDBACK ---")
        sections.append("\n".join(lines))

    if not sections:
        return None

    return "\n\n".join(sections)
