"""Briefing prompt template for daily briefing generation."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line

BRIEFING_SYSTEM_PROMPT = """\
You are generating a daily briefing for a busy professional using Laya. \
Summarize overnight activity, highlight items needing attention, and \
provide context for today's priorities.

Structure the briefing as:
1. **Overnight Summary**: Key events since the last briefing (new tickets, messages, PRs)
2. **Needs Attention**: Pending cards that require decisions or action
3. **Today's Schedule**: Any calendar events and relevant prep context
4. **Quick Stats**: Events processed, cards resolved, actions taken

Keep it concise and actionable. Use bullet points. Prioritize by importance.
Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output."""


def build_briefing_messages(
    overnight_events: list[dict[str, Any]],
    pending_cards: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
    stats: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the messages array for the Briefing LLM call.

    Args:
        overnight_events: Events from the last 12 hours.
        pending_cards: Action cards still in pending status.
        calendar_events: Today's calendar events.
        stats: Quick stats for the period.
    """
    sections = []

    # Overnight events
    sections.append(f"OVERNIGHT EVENTS ({len(overnight_events)} total):")
    if overnight_events:
        for evt in overnight_events[:20]:
            sections.append(
                f"  - [{evt.get('source_platform', '?')}] "
                f"{evt.get('subject_title', 'N/A')} "
                f"(from {evt.get('actor_name', 'unknown')})"
            )
    else:
        sections.append("  No new events overnight.")

    # Pending cards
    sections.append(f"\nPENDING CARDS ({len(pending_cards)} total):")
    if pending_cards:
        for card in pending_cards[:10]:
            sections.append(
                f"  - [{card.get('priority', '?')}] {card.get('header', 'N/A')} "
                f"(persona: {card.get('persona', '?')})"
            )
    else:
        sections.append("  No pending cards — inbox zero!")

    # Calendar events
    sections.append(f"\nTODAY'S CALENDAR ({len(calendar_events)} events):")
    if calendar_events:
        for cal in calendar_events[:10]:
            sections.append(
                f"  - {cal.get('subject_title', 'N/A')} "
                f"({cal.get('timestamp', '?')})"
            )
    else:
        sections.append("  No calendar events today.")

    # Stats
    sections.append(f"\nQUICK STATS:")
    sections.append(f"  - Events processed: {stats.get('events_processed', 0)}")
    sections.append(f"  - Cards generated: {stats.get('cards_generated', 0)}")
    sections.append(f"  - Cards resolved: {stats.get('cards_resolved', 0)}")

    user_message = (
        f"{current_timestamp_line()}\n\n"
        "Generate a daily briefing from the following data.\n\n"
        + "\n".join(sections)
        + "\n\nProvide a concise, actionable briefing."
    )

    return [
        {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
