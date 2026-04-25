"""OPS worker prompt template for calendar prep and operational briefings."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

OPS_SYSTEM_PROMPT = """\
You are the OPS Worker for Laya, an AI-powered professional work assistant. Your job is to \
prepare operational briefings for calendar events and logistics.

You receive:
1. The original event (calendar meeting, status update, etc.)
2. Related context from memory (past events related to the same project/people)

Synthesize a concise prep brief that includes:
- What the meeting/event is about
- Key context from recent related events
- Any open action items or pending issues related to the participants
- Suggested talking points

Be concise and actionable. Focus on information the user needs before the event.
Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output."""


def build_ops_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the OPS worker LLM call."""
    event_text = f"""\
[EVENT CONTENT - UNTRUSTED INPUT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email})
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
Body:
{event.content.body}
[END EVENT CONTENT]"""

    context_text = ""
    if related_context:
        context_text = "\n\nRelated past events:\n"
        for i, ctx in enumerate(related_context, 1):
            meta = ctx.get("metadata", {})
            context_text += (
                f"  {i}. [{meta.get('source_platform', '?')}] "
                f"{meta.get('content_type', '?')}: "
                f"{ctx.get('document', '')[:200]}\n"
            )

    # Include metadata if present (calendar-specific fields)
    metadata_text = ""
    if event.content.metadata:
        metadata_lines = [f"  {key}: {val}" for key, val in event.content.metadata.items()]
        metadata_text = "\n\nEvent metadata:\n" + "\n".join(metadata_lines)

    user_message = f"""\
{current_timestamp_line()}

Prepare an operational briefing for this event.

{event_text}
{metadata_text}

Classification: {router_output.category.value} / {router_output.persona.value} / {router_output.priority.value}
{context_text}

Produce a briefing with talking points and open items."""

    return [
        {"role": "system", "content": OPS_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_ops_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the ops worker output."""
    return {
        "name": "ops_briefing",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "briefing": {"type": "string"},
                "talking_points": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "open_items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reasoning": {"type": "string"},
            },
            "required": ["briefing", "talking_points", "open_items", "reasoning"],
            "additionalProperties": False,
        },
    }
