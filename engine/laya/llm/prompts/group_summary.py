# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Prompt templates for entity group rolling summaries."""

from __future__ import annotations

import json
from typing import Any

from laya.llm.prompts.overrides import get_prompt

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

GROUP_SUMMARY_INITIAL_SYSTEM_PROMPT = """\
You are a concise status synthesizer for a professional event tracking system.
Given multiple action cards about the same entity (e.g., a pull request, email \
thread, Jira ticket, Slack conversation), produce a rolling executive summary \
that captures the entity's journey and current state.

## OUTPUT FORMAT (JSON)

Return a JSON object with these fields:
- **headline**: One-liner current status (max 100 characters, present tense). \
  Example: "PR #540 approved, awaiting merge"
- **summary**: 3-5 sentence narrative of the entity's journey and current state. \
  Start with what the entity is, cover key milestones, end with where things stand now.
- **key_events**: Chronological list of significant developments (3-7 items, \
  newest last). Each item is an object with two fields:
  - **event**: One concise sentence describing what happened and who did it. \
    Do NOT include timestamps or dates in this text.
  - **timestamp**: ISO 8601 UTC timestamp (e.g. "2026-05-04T04:23:03Z") for \
    when the event occurred. Use the card's date/time. If the exact time is \
    unknown, use an empty string "".
- **current_status**: One sentence describing what is happening RIGHT NOW with \
  this entity.
- **pending_actions**: Array of items that still need attention. Null if nothing \
  is pending or all items are resolved.

## RULES

- Be synthesis-oriented, not exhaustive — highlight what matters.
- Use present tense for current state, past tense for history.
- Include actor names when relevant (who did what).
- Reference specific details (PR numbers, ticket IDs, dates, branch names).
- pending_actions should only list genuinely open items, not completed ones.
- If all activity is resolved (merged, closed, completed), set pending_actions to null.
- Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output.
"""

GROUP_SUMMARY_ROLLING_SYSTEM_PROMPT = """\
You are updating a rolling executive summary for a tracked entity in a \
professional event tracking system.

You receive the CURRENT summary and one or more NEW event cards. Your job is to \
integrate the new events into the existing summary, producing an updated version.

## TASK

Integrate the new event(s) into the existing summary:
- If a new event **resolves** something mentioned in the current summary, \
  update the headline, current_status, and remove it from pending_actions.
- If it **adds new information**, incorporate it naturally into the summary narrative.
- If it **changes the entity's status** (e.g., PR merged, ticket closed, email replied), \
  update headline and current_status to reflect the new state.
- Keep key_events chronological (oldest first, newest last). Add new developments \
  as objects with "event" and "timestamp" fields. If existing key_events contain \
  plain strings (legacy format), convert them to {{"event": <text>, "timestamp": ""}} \
  objects. If there are more than 7 items, prune the least significant older entries.
- Don't just append — **synthesize** into a coherent narrative.
- pending_actions: remove items that are now resolved, add new ones if applicable.
- When multiple new cards are provided, integrate ALL of them in a single pass.
- Never use emoji or icon characters anywhere in your output.

## OUTPUT FORMAT (JSON)

Same structure as the current summary:
- **headline**: Updated one-liner (max 100 chars, present tense)
- **summary**: Updated 3-5 sentence narrative
- **key_events**: Updated chronological list (3-7 items)
- **current_status**: Updated one sentence for what's happening now
- **pending_actions**: Updated list, or null if nothing pending
"""

# ---------------------------------------------------------------------------
# JSON schema for structured output
# ---------------------------------------------------------------------------

GROUP_SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "name": "group_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "key_events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "event": {"type": "string"},
                        "timestamp": {"type": "string"},
                    },
                    "required": ["event", "timestamp"],
                    "additionalProperties": False,
                },
            },
            "current_status": {"type": "string"},
            "pending_actions": {
                "type": ["array", "null"],
                "items": {"type": "string"},
            },
        },
        "required": ["headline", "summary", "key_events", "current_status", "pending_actions"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _serialize_card(card: dict) -> str:
    """Serialize a card row dict into a compact text block for the LLM."""
    parts = [f"[Card {card.get('card_id', '?')}]"]
    if card.get("created_at"):
        parts.append(f"  Date: {card['created_at']}")
    if card.get("platform") or card.get("entity_id"):
        plat = card.get("platform") or (card["entity_id"].split(":")[0] if card.get("entity_id") and ":" in card["entity_id"] else "")
        parts.append(f"  Platform: {plat}")
    if card.get("actor_name"):
        parts.append(f"  Actor: {card['actor_name']}")
    if card.get("status"):
        parts.append(f"  Status: {card['status']}")
    parts.append(f"  Title: {card.get('header', '')}")
    parts.append(f"  Summary: {card.get('summary', '')}")
    intelligence = card.get("intelligence")
    if intelligence:
        if isinstance(intelligence, str):
            try:
                intelligence = json.loads(intelligence)
            except (json.JSONDecodeError, TypeError):
                intelligence = None
        if isinstance(intelligence, list) and intelligence:
            parts.append("  Key points:")
            for item in intelligence[:5]:
                parts.append(f"    - {item}")
    if card.get("tags"):
        tags = card["tags"] if isinstance(card["tags"], str) else ", ".join(card["tags"])
        parts.append(f"  Tags: {tags}")
    return "\n".join(parts)


def build_initial_messages(
    cards: list[dict],
    entity_id: str,
) -> list[dict[str, str]]:
    """Build LLM messages for initial summary generation (2+ cards)."""
    system = get_prompt("group_summary_initial", GROUP_SUMMARY_INITIAL_SYSTEM_PROMPT)

    cards_text = "\n\n".join(_serialize_card(c) for c in cards)
    user_msg = (
        f"Entity: {entity_id}\n"
        f"Total cards: {len(cards)}\n\n"
        f"--- CARDS ---\n\n{cards_text}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


def build_rolling_messages(
    existing_summary: dict,
    new_cards: list[dict] | dict,
    entity_id: str,
) -> list[dict[str, str]]:
    """Build LLM messages for rolling summary update.

    Accepts a single card dict (legacy) or a list of cards (batched).
    """
    system = get_prompt("group_summary_rolling", GROUP_SUMMARY_ROLLING_SYSTEM_PROMPT)

    # Support both single card (legacy callers) and batched list
    if isinstance(new_cards, dict):
        new_cards = [new_cards]

    cards_section = "\n\n".join(_serialize_card(c) for c in new_cards)
    label = "NEW EVENT CARDS" if len(new_cards) > 1 else "NEW EVENT CARD"

    user_msg = (
        f"Entity: {entity_id}\n\n"
        f"--- CURRENT SUMMARY ---\n{json.dumps(existing_summary, indent=2)}\n\n"
        f"--- {label} ---\n{cards_section}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


# ---------------------------------------------------------------------------
# Context-group summary (built from entity summaries, not raw cards)
# ---------------------------------------------------------------------------

CONTEXT_SUMMARY_SYSTEM_PROMPT = """\
You are a concise status synthesizer for a professional event tracking system.
You are summarizing a group of related work items that span different platforms \
or entities. You receive entity-level summaries (already distilled) and \
optionally some individual cards for entities without summaries.

Produce a cross-cutting executive summary that highlights the connections \
between these items, their dependencies, and the overall status.

## OUTPUT FORMAT (JSON)

Return a JSON object with these fields:
- **headline**: One-liner capturing the overall situation across all items \
  (max 100 characters, present tense).
- **summary**: 3-5 sentence narrative synthesizing how these items relate, \
  their combined journey, and where things stand overall. Focus on the \
  connections and dependencies between entities, not just restating each one.
- **key_events**: Chronological list of the most significant developments \
  across all entities (3-7 items, newest last). Each item is an object with:
  - **event**: One concise sentence.
  - **timestamp**: ISO 8601 UTC timestamp or "" if unknown.
- **current_status**: One sentence describing the overall state of this \
  group of related items right now.
- **pending_actions**: Array of open items across all entities, or null if \
  nothing pending.

## RULES

- Synthesize across entities — don't just concatenate individual summaries.
- Highlight cause-and-effect between items (e.g., "Jira ticket triggered the PR").
- Use present tense for current state, past tense for history.
- Include platform context (which item is from Jira, Gmail, etc.) when useful.
- Never use emoji or icon characters anywhere in your output.
"""


def _serialize_entity_summary(summary: dict, entity_id: str) -> str:
    """Format an entity-level summary for inclusion in a context summary prompt."""
    plat = entity_id.split(":")[0] if ":" in entity_id else "unknown"
    subject = entity_id.split(":")[-1] if ":" in entity_id else entity_id
    parts = [
        f"[Entity: {subject} ({plat})]",
        f"  Headline: {summary.get('headline', '')}",
        f"  Summary: {summary.get('summary', '')}",
    ]
    if summary.get("current_status"):
        parts.append(f"  Current status: {summary['current_status']}")
    pending = summary.get("pending_actions")
    if pending:
        parts.append("  Pending actions:")
        for item in pending:
            parts.append(f"    - {item}")
    return "\n".join(parts)


def build_context_summary_messages(
    entity_summaries: list[tuple[str, dict]],
    fallback_cards: list[dict],
    context_label: str | None,
) -> list[dict[str, str]]:
    """Build LLM messages for context-group summary generation.

    Args:
        entity_summaries: List of (entity_id, summary_dict) tuples.
        fallback_cards: Raw cards for entities that lack summaries.
        context_label: Human-readable label for the context group.
    """
    system = get_prompt("context_summary", CONTEXT_SUMMARY_SYSTEM_PROMPT)

    sections: list[str] = []
    if context_label:
        sections.append(f"Context: {context_label}")

    if entity_summaries:
        summaries_text = "\n\n".join(
            _serialize_entity_summary(s, eid) for eid, s in entity_summaries
        )
        sections.append(f"--- ENTITY SUMMARIES ---\n\n{summaries_text}")

    if fallback_cards:
        cards_text = "\n\n".join(_serialize_card(c) for c in fallback_cards)
        sections.append(
            f"--- INDIVIDUAL CARDS (no entity summary available) ---\n\n{cards_text}"
        )

    user_msg = "\n\n".join(sections)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
