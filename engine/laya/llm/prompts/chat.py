"""Chat prompt template for conversational interactions."""

from __future__ import annotations

from typing import Any

CHAT_SYSTEM_PROMPT = """\
You are Laya, a professional AI assistant that helps users understand and manage \
their work events, action cards, and team context.

You have access to the user's event history, action cards, and team context from \
their connected tools (Jira, Bitbucket, Slack, Gmail, Calendar).

When referencing specific cards, use the format [card:CARD_ID] so the UI can \
create clickable links. When referencing events, use [event:EVENT_ID].

Guidelines:
- Be concise and professional
- Reference specific cards and events when relevant
- If you're unsure about something, say so
- Summarize findings clearly with bullet points when appropriate
- Include relevant IDs and links to help the user navigate"""


def build_chat_messages(
    user_message: str,
    chat_history: list[dict[str, str]],
    context: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the messages array for the Chat LLM call.

    Args:
        user_message: The user's current message.
        chat_history: Recent chat messages in OpenAI format.
        context: Retrieved context (cards, events, memory results).
    """
    # Build context section
    context_parts = []

    related_cards = context.get("related_cards", [])
    if related_cards:
        context_parts.append("Related action cards:")
        for card in related_cards[:5]:
            context_parts.append(
                f"  - [{card.get('card_id')}] {card.get('header', 'N/A')} "
                f"(status: {card.get('status', '?')}, priority: {card.get('priority', '?')})"
            )

    related_events = context.get("related_events", [])
    if related_events:
        context_parts.append("Related events:")
        for evt in related_events[:5]:
            context_parts.append(
                f"  - [{evt.get('event_id')}] [{evt.get('source_platform', '?')}] "
                f"{evt.get('subject_title', 'N/A')}"
            )

    memory_results = context.get("memory_results", [])
    if memory_results:
        context_parts.append("Related past context (semantic search):")
        for i, mem in enumerate(memory_results[:3], 1):
            doc = mem.get("document", "")[:200]
            meta = mem.get("metadata", {})
            context_parts.append(
                f"  {i}. [{meta.get('source_platform', '?')}] {doc}..."
            )

    context_section = ""
    if context_parts:
        context_section = (
            "\n\n[RETRIEVED CONTEXT]\n"
            + "\n".join(context_parts)
            + "\n[END CONTEXT]"
        )

    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    # Add recent chat history for conversation continuity
    for msg in chat_history[-10:]:
        messages.append(msg)

    # Add current user message with context
    user_content = user_message
    if context_section:
        user_content = f"{user_message}{context_section}"

    messages.append({"role": "user", "content": user_content})

    return messages
