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


TRACE_SUMMARY_SYSTEM = """\
You are Laya's Trace analyst. Given the clusters discovered by a coherence search, \
write a comprehensive summary that ties together the full picture across all clusters.

Rules:
- Write a professional executive summary (5-10 sentences).
- Start with the overarching theme or connection between these clusters.
- Highlight cross-cluster dependencies and patterns (e.g., "the same PR appears in \
  both the Jira ticket and the Slack discussion").
- Call out which items are resolved vs still open/blocked.
- Note any risk areas: stale tickets, PRs waiting for review, threads with no resolution.
- Mention the platforms involved and the time span.
- Be specific: include identifiers, names, and dates.
- Do NOT include preamble like "Here is a summary" — start narrating directly.
"""


def build_summary_messages(
    query: str, clusters: list[TraceCluster]
) -> list[dict[str, str]]:
    """Build the messages list for the overall trace summary LLM call."""
    parts: list[str] = [f"Search query: {query}", f"Total clusters: {len(clusters)}", ""]

    for cluster in clusters:
        entity = cluster.primary_entity
        parts.append(f"--- Cluster: {entity.title} ({entity.platform}) ---")
        parts.append(f"Cards: {cluster.status_summary.total_cards}")
        parts.append(f"Platforms: {', '.join(cluster.status_summary.platforms_involved)}")
        parts.append(f"State: {cluster.status_summary.current_state}")
        dr = cluster.status_summary.date_range
        parts.append(f"Date range: {dr.get('from', '?')} to {dr.get('to', '?')}")
        if cluster.status_summary.pending_actions > 0:
            parts.append(f"Pending actions: {cluster.status_summary.pending_actions}")
        if cluster.linked_entities:
            linked = ", ".join(f"{e.title} ({e.platform})" for e in cluster.linked_entities)
            parts.append(f"Linked: {linked}")
        if cluster.narrative:
            parts.append(f"Narrative: {cluster.narrative[:300]}")

        # Include a few card headlines for context
        for card in cluster.timeline[:5]:
            actor = f" by {card.actor_name}" if card.actor_name else ""
            parts.append(f"  - [{card.created_at or '?'}] {card.header}{actor}")

        parts.append("")

    user_content = "\n".join(parts)
    if len(user_content) > 6000:
        user_content = user_content[:6000] + "\n\n[... additional data truncated]"

    return [
        {"role": "system", "content": TRACE_SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]


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
