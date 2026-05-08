"""Prompt templates for entity group rolling summaries."""

from __future__ import annotations

import json
from typing import Any

from laya.llm.prompts import current_timestamp_line

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

GROUP_SUMMARY_INITIAL_SYSTEM_PROMPT = """\
You are a concise status synthesizer for a professional event tracking system.
Given multiple action cards about the same entity (e.g., a pull request, email \
thread, Jira ticket, Slack conversation), produce a rolling executive summary \
that captures the entity's journey and current state.

{timestamp}

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

{timestamp}

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
    ts = current_timestamp_line()
    system = GROUP_SUMMARY_INITIAL_SYSTEM_PROMPT.format(timestamp=ts)

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
    ts = current_timestamp_line()
    system = GROUP_SUMMARY_ROLLING_SYSTEM_PROMPT.format(timestamp=ts)

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
