"""Summarizer prompt template — incrementally build a running daily summary."""

from __future__ import annotations

from typing import Any

SUMMARIZER_SYSTEM_PROMPT = """\
You are the Daily Summarizer for Laya, a professional AI operating system. Your job is \
to maintain a running summary of the user's day by incrementally incorporating new events.

You receive:
1. The current running summary for the day (may be empty if this is the first event)
2. A new card that was just processed (header, summary, priority, category, intelligence)
3. Optionally, a status change on an existing card

You must produce an updated summary with these sections:

- **events_and_meetings**: Important events, meetings, communications, and notable happenings. \
Each item should be a concise one-liner capturing what happened.
- **action_items**: Tasks the user needs to act on — reviews, replies, approvals, fixes. \
Track whether items are still pending or have been resolved.
- **key_updates**: Status changes, deployments, decisions, and other noteworthy updates \
that don't require action but are good to know.

Rules:
- Be concise. Each item should be 1 short sentence.
- When incorporating a new card, add relevant items to the appropriate sections.
- When a card status changes (approved, dismissed, completed), update the relevant items \
to reflect the new status. Mark resolved items with status "done" or "dismissed".
- Do NOT remove items — keep them for the full day's record, but update their status.
- Do NOT duplicate items. If a new card relates to an existing summary item, update it.
- Prioritize clarity and usefulness for a busy professional scanning their day.
- Each item MUST include the card_id it relates to for navigation.
- Order items by priority/importance within each section."""

SUMMARIZER_STATUS_CHANGE_PROMPT = """\
You are the Daily Summarizer for Laya. A card's status has changed. Update the running \
summary to reflect this change. Mark the relevant item(s) as resolved/dismissed/completed \
as appropriate. Do NOT remove items — update their status field instead.

Rules:
- Find items referencing the changed card_id and update their status.
- If the card was approved/completed, set status to "done".
- If the card was dismissed, set status to "dismissed".
- If the card was archived, set status to "archived".
- If the card was reopened, set status back to "pending".
- Keep all other items unchanged.
- Return the full updated summary."""


def build_summarizer_messages(
    current_summary: dict[str, Any] | None,
    card_header: str,
    card_summary: str,
    card_priority: str,
    card_category: str,
    card_id: str,
    card_intelligence: list[str] | None = None,
    card_persona: str | None = None,
    actor_name: str | None = None,
    source_platform: str | None = None,
) -> list[dict[str, str]]:
    """Build messages for incorporating a new card into the daily summary."""
    current_text = ""
    if current_summary:
        import json
        current_text = f"\n\n[CURRENT SUMMARY]\n{json.dumps(current_summary, indent=2)}\n[END CURRENT SUMMARY]"
    else:
        current_text = "\n\n[CURRENT SUMMARY]\nEmpty — this is the first event of the day.\n[END CURRENT SUMMARY]"

    intel_text = ""
    if card_intelligence:
        intel_text = "\nKey findings:\n" + "\n".join(f"  - {i}" for i in card_intelligence[:5])

    user_message = f"""\
Update the daily summary to incorporate this new card.
{current_text}

[NEW CARD]
Card ID: {card_id}
Header: {card_header}
Summary: {card_summary}
Priority: {card_priority}
Category: {card_category}
Persona: {card_persona or 'N/A'}
Platform: {source_platform or 'N/A'}
Actor: {actor_name or 'N/A'}{intel_text}
[END NEW CARD]

Produce the updated summary JSON matching the required schema."""

    return [
        {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def build_status_change_messages(
    current_summary: dict[str, Any],
    card_id: str,
    card_header: str,
    new_status: str,
) -> list[dict[str, str]]:
    """Build messages for updating the summary when a card status changes."""
    import json

    user_message = f"""\
A card's status has changed. Update the daily summary accordingly.

[CURRENT SUMMARY]
{json.dumps(current_summary, indent=2)}
[END CURRENT SUMMARY]

[STATUS CHANGE]
Card ID: {card_id}
Card Header: {card_header}
New Status: {new_status}
[END STATUS CHANGE]

Produce the full updated summary JSON matching the required schema."""

    return [
        {"role": "system", "content": SUMMARIZER_STATUS_CHANGE_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_summarizer_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the summarizer output."""
    item_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Concise one-line description of the item",
            },
            "card_id": {
                "type": "string",
                "description": "The card_id this item relates to",
            },
            "priority": {
                "type": "string",
                "description": "CRITICAL, HIGH, MEDIUM, or LOW",
            },
            "status": {
                "type": "string",
                "description": "pending, done, dismissed, or archived",
            },
        },
        "required": ["text", "card_id", "priority", "status"],
        "additionalProperties": False,
    }

    return {
        "name": "daily_summary",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "events_and_meetings": {
                    "type": "array",
                    "items": item_schema,
                    "description": "Important events, meetings, and communications",
                },
                "action_items": {
                    "type": "array",
                    "items": item_schema,
                    "description": "Tasks requiring user action",
                },
                "key_updates": {
                    "type": "array",
                    "items": item_schema,
                    "description": "Status changes, deployments, decisions — FYI items",
                },
            },
            "required": ["events_and_meetings", "action_items", "key_updates"],
            "additionalProperties": False,
        },
    }
