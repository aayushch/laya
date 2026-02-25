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
        rows = await db.execute_fetchall(
            """SELECT
                   e.source_platform,
                   e.source_raw_event_type,
                   ac.persona,
                   ac.priority,
                   ac.status,
                   COUNT(*) as count
               FROM action_cards ac
               JOIN events e ON ac.event_id = e.event_id
               WHERE e.source_platform = ?
                 AND ac.status IN ('approved', 'dismissed')
                 AND ac.resolved_at IS NOT NULL
                 AND ac.created_at > datetime('now', '-30 days')
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


def format_feedback_section(patterns: list[dict]) -> str | None:
    """Format feedback patterns into a prompt section for the router.

    Groups patterns by (platform, event_type) and computes approval/dismissal stats.

    Returns:
        Formatted string for prompt injection, or None if no patterns.
    """
    if not patterns:
        return None

    # Group by (platform, event_type)
    groups: dict[tuple[str, str], dict] = {}
    for p in patterns:
        key = (p["source_platform"], p["event_type"])
        if key not in groups:
            groups[key] = {"approved": 0, "dismissed": 0, "details": []}
        if p["status"] == "approved":
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

        # Show per-priority breakdown if multiple priorities exist
        priority_stats: dict[str, dict] = {}
        for d in group["details"]:
            prio = d["priority"]
            if prio not in priority_stats:
                priority_stats[prio] = {"approved": 0, "dismissed": 0}
            if d["status"] == "approved":
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
    return "\n".join(lines)
