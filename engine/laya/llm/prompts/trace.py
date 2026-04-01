"""LLM prompt for Trace narrative generation."""

from __future__ import annotations

from laya.models.trace import TraceCluster


TRACE_NARRATIVE_SYSTEM = """\
You are Laya's Trace narrator. Given a set of action cards related to a single \
work entity (a ticket, PR, project, etc.), write a concise narrative that tells \
the story of what happened.

Rules:
- Write 3-6 sentences of professional prose. No bullet lists, no markdown headers.
- Cover: what the entity is, key events in chronological order, which platforms \
  were involved, and anything that needs attention.
- Derive the current state of the entity (e.g., whether a PR is merged, a ticket \
  is resolved) from the event content and summaries — NOT from the Priority or \
  any internal metadata fields. Priority reflects how urgent Laya considered the \
  notification, not the importance of the entity itself.
- Highlight cross-platform correlations (e.g., "the Jira ticket was discussed on \
  Slack and a PR was opened on GitHub").
- Use past tense for completed events, present tense for current state.
- Be specific: include ticket IDs, PR numbers, people's names, and dates.
- If there are pending actions or blockers, mention them at the end.
- Do NOT include preamble like "Here is a summary" — just start narrating.
"""


def build_narrative_messages(clusters: list[TraceCluster]) -> list[dict[str, str]]:
    """Build the messages list for the narrative LLM call."""
    # Collect all cards from all clusters into a structured summary
    parts: list[str] = []

    for cluster in clusters:
        entity = cluster.primary_entity
        parts.append(f"Entity: {entity.title} ({entity.platform}, {entity.entity_id})")

        if cluster.linked_entities:
            linked = ", ".join(f"{e.title} ({e.platform})" for e in cluster.linked_entities)
            parts.append(f"Linked entities: {linked}")

        parts.append(f"Platforms: {', '.join(cluster.status_summary.platforms_involved)}")
        parts.append(
            f"Date range: {cluster.status_summary.date_range.get('from', '?')} "
            f"to {cluster.status_summary.date_range.get('to', '?')}"
        )
        parts.append(f"Total cards: {cluster.status_summary.total_cards}")
        parts.append("")

        for i, card in enumerate(cluster.timeline, 1):
            platform = ""
            if card.entity_id and ":" in card.entity_id:
                platform = card.entity_id.split(":")[0]

            actor = ""
            if card.actor_name:
                actor = f" by {card.actor_name}"

            parts.append(
                f"{i}. [{card.created_at or '?'}] [{platform or '?'}] "
                f"{card.header}{actor}"
            )
            if card.summary:
                # Truncate long summaries to keep context manageable
                summary = card.summary[:200]
                parts.append(f"   {summary}")
            parts.append("")

    user_content = "\n".join(parts)

    # Cap at ~3000 chars to stay within token limits
    if len(user_content) > 3000:
        user_content = user_content[:3000] + "\n\n[... additional cards truncated]"

    return [
        {"role": "system", "content": TRACE_NARRATIVE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
