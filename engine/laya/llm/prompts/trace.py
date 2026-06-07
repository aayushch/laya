# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""LLM prompt for Trace narrative generation."""

from __future__ import annotations

from laya.llm.prompts.overrides import get_prompt
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
- Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output.

## User Identity

If a [LAYA USER] block is provided, it identifies the person reading this summary. \
Use this to correctly attribute actions: distinguish between what the Laya user did \
and what other actors did. Never confuse the PR author, ticket reporter, or commenter \
with the Laya user unless they are actually the same person (matched by name or email). \
The actor_name on each card tells you WHO performed that action — use it faithfully.

## Participant Roles

If participant role information is provided (e.g., author, reviewer, assignee), use it \
to correctly frame the narrative. For example, if the Laya user is a "reviewer" on a PR, \
say "you reviewed" or "awaiting your review", not "you authored". If the Laya user is \
the "assignee" on a ticket, frame updates as relevant to their work on it.
"""


TRACE_SUMMARY_SYSTEM = """\
You are Laya's Trace analyst. Given the clusters discovered by a coherence search, \
write a comprehensive summary that ties together the full picture across all clusters.

Your summary must cover two dimensions:

1. THEMATIC COVERAGE — What these tasks collectively represent as a body of work. \
Synthesize the subject areas, themes, and deliverables across all clusters. Be specific \
(name data types, features, components, patterns, etc.) but aggregate — do not list every \
item individually. Example: "These 28 tasks investigate false positives and false negatives \
across 15+ PII data types including credit card numbers, driver licenses, DEA numbers, and \
phone numbers, as part of the Counterfact Dataset Improvement initiative."

2. OPERATIONAL STATE — The current status of this body of work. What is resolved vs \
still open/blocked, cross-cluster dependencies, risk areas (stale tickets, PRs waiting \
for review, threads with no resolution), and any items needing attention.

Rules:
- Write 8-15 sentences of professional prose. No bullet lists, no markdown headers.
- Start with the thematic overview, then transition to operational state.
- Be specific: include identifiers, names, and dates where they add clarity.
- Mention the platforms involved and the time span.
- Do NOT include preamble like "Here is a summary" — start narrating directly.
- Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output.

## User Identity

If a [LAYA USER] block is provided, it identifies the person reading this summary. \
Use this to correctly attribute actions — pay close attention to the actor_name on \
each card to determine WHO actually performed an action. Do NOT confuse roles: if \
the Laya user opened a PR, say so; if someone else authored it, name them correctly. \
Never assume the PR author or ticket reporter based on who the Laya user is — always \
derive authorship from the card data (actor_name fields and event content).

## Participant Roles

If participant role information is provided (e.g., author, reviewer, assignee), use it \
to correctly frame the summary. The Laya user's role tells you their relationship to \
the work item — use it to personalize the summary appropriately.
"""


def _identity_block(user_identity: dict[str, str] | None) -> str:
    """Return a [LAYA USER] context block, or empty string if not configured."""
    if not user_identity:
        return ""
    name = user_identity.get("name", "Unknown")
    email = user_identity.get("email", "")
    emails = user_identity.get("emails", [email] if email else [])
    return (
        f"\n[LAYA USER]\n"
        f"Name: {name}\n"
        f"Emails: {', '.join(emails)}\n"
        f"[END LAYA USER]\n"
    )


def _build_cluster_topic(
    cards: list,
    budget: int = 600,
) -> str:
    """Build a middle-truncated topic string from card summaries.

    First and last card summaries are always kept in full. Middle summaries
    are uniformly sampled to fit within *budget* characters.
    """
    summaries = [c.summary for c in cards if c.summary]
    if not summaries:
        return ""
    if len(summaries) == 1:
        return summaries[0]

    first = summaries[0]
    last = summaries[-1]
    middle = summaries[1:-1]

    reserved = len(first) + len(last)
    remaining_budget = budget - reserved
    if remaining_budget < 0:
        remaining_budget = 0

    if not middle:
        return f"{first} | {last}"

    middle_combined = " | ".join(middle)
    if len(middle_combined) <= remaining_budget:
        return f"{first} | {middle_combined} | {last}"

    # Uniformly sample middle summaries that fit within budget
    kept: list[str] = []
    # Try progressively fewer samples until they fit
    for sample_count in range(len(middle), 0, -1):
        step = len(middle) / sample_count
        candidates = [middle[int(i * step)] for i in range(sample_count)]
        total = sum(len(s) for s in candidates) + (sample_count - 1) * 3  # " | " separators
        if total <= remaining_budget:
            kept = candidates
            break

    dropped = len(middle) - len(kept)
    if kept:
        middle_str = " | ".join(kept)
        if dropped > 0:
            middle_str += f" [... {dropped} more updates ...]"
        return f"{first} | {middle_str} | {last}"
    else:
        return f"{first} [... {len(middle)} more updates ...] {last}"


def build_summary_messages(
    query: str, clusters: list[TraceCluster],
    user_identity: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build the messages list for the overall trace summary LLM call."""
    parts: list[str] = [_identity_block(user_identity), f"Search query: {query}", f"Total clusters: {len(clusters)}", ""]

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

        # Deduplicated headers
        seen_headers: set[str] = set()
        unique_headers: list[str] = []
        for card in cluster.timeline:
            if card.header not in seen_headers:
                seen_headers.add(card.header)
                unique_headers.append(card.header)
        parts.append(f"Headers: {' | '.join(unique_headers)}")

        # Thematic topic from card summaries (middle-truncated)
        topic = _build_cluster_topic(cluster.timeline)
        if topic:
            parts.append(f"Topic: {topic}")

        parts.append("")

    user_content = "\n".join(parts)
    if len(user_content) > 12000:
        user_content = user_content[:12000] + "\n\n[... additional data truncated]"

    return [
        {"role": "system", "content": get_prompt("trace_summary", TRACE_SUMMARY_SYSTEM)},
        {"role": "user", "content": user_content},
    ]


def build_narrative_messages(
    clusters: list[TraceCluster],
    user_identity: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build the messages list for the narrative LLM call."""
    # Collect all cards from all clusters into a structured summary
    parts: list[str] = [_identity_block(user_identity), ""]

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
        {"role": "system", "content": get_prompt("trace_narrative", TRACE_NARRATIVE_SYSTEM)},
        {"role": "user", "content": user_content},
    ]
