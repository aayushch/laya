"""FINANCE worker prompt template for financial briefings and synthesis."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

FINANCE_SYSTEM_PROMPT = """\
You are the FINANCE Worker for Laya, an AI-powered professional work assistant. Your job \
is to synthesize financial events into a concise briefing the user can act on.

You receive:
1. The original event (invoice, expense report, budget update, revenue number, vendor \
contract, purchase approval request, financial review meeting, etc.)
2. Related context from memory (past events related to the same vendor / budget / \
period)

Produce a brief that covers:
- What the financial event is and why it matters
- Key figures (amounts, deltas vs. budget/plan, period)
- Open items that need the user's decision or review
- Suggested actions (approve, flag, forward, schedule review, request more info)

Be precise with numbers. Never invent figures the event does not provide. Flag anything \
that looks anomalous (unusually large amount, budget overrun, missing approval, policy \
concern) in the briefing."""


def build_finance_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the FINANCE worker LLM call."""
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

    metadata_text = ""
    if event.content.metadata:
        metadata_lines = [f"  {key}: {val}" for key, val in event.content.metadata.items()]
        metadata_text = "\n\nEvent metadata:\n" + "\n".join(metadata_lines)

    user_message = f"""\
{current_timestamp_line()}

Prepare a financial briefing for this event.

{event_text}
{metadata_text}

Classification: {router_output.category.value} / {router_output.persona.value} / {router_output.priority.value}
{context_text}

Produce a briefing with key_figures, open_items, and suggested_actions."""

    return [
        {"role": "system", "content": FINANCE_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_finance_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the finance worker output."""
    return {
        "name": "finance_briefing",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "briefing": {"type": "string"},
                "key_figures": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "open_items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "suggested_actions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reasoning": {"type": "string"},
            },
            "required": [
                "briefing",
                "key_figures",
                "open_items",
                "suggested_actions",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    }
