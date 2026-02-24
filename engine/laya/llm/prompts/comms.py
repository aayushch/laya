"""COMMS worker prompt template for drafting communication replies."""

from __future__ import annotations

from typing import Any

from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

COMMS_SYSTEM_PROMPT = """\
You are the COMMS Worker for Laya, a professional AI operating system. Your job is to \
draft professional replies to communications (Slack messages, emails, Jira comments).

You receive:
1. The original event (Slack message, email, etc.)
2. The Router's classification
3. Related context from memory
4. Optionally, findings from a prior ENGINEER worker

Draft a clear, professional, contextually appropriate reply. Match the tone of the \
original message. If the event includes technical findings from a prior worker, \
incorporate them naturally into the reply.

Output the drafted reply text, the tone you chose, and a brief explanation of your approach."""


def build_comms_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict[str, Any]] | None = None,
    prior_findings: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the COMMS worker LLM call."""
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
                f"{ctx.get('document', '')[:200]}\n"
            )

    findings_text = ""
    if prior_findings:
        findings_text = f"\n\nFindings from prior ENGINEER worker:\n{_summarize_findings(prior_findings)}"

    user_message = f"""\
Draft a professional reply to this communication.

{event_text}

Classification: {router_output.category.value} / {router_output.persona.value} / {router_output.priority.value}
{context_text}{findings_text}

Respond with a drafted reply, the tone, and your reasoning."""

    return [
        {"role": "system", "content": COMMS_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def _summarize_findings(findings: dict[str, Any]) -> str:
    """Summarize findings dict into readable text."""
    parts = []
    if "agent_result" in findings:
        parts.append(f"Agent result: {str(findings['agent_result'])[:500]}")
    if "draft" in findings:
        draft = findings["draft"]
        if isinstance(draft, dict):
            parts.append(f"Draft: {draft.get('draft_reply', str(draft))[:500]}")
        else:
            parts.append(f"Draft: {str(draft)[:500]}")
    if not parts:
        parts.append(str(findings)[:500])
    return "\n".join(parts)


def get_comms_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the comms worker output."""
    return {
        "name": "comms_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "draft_reply": {"type": "string"},
                "tone": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["draft_reply", "tone", "reasoning"],
            "additionalProperties": False,
        },
    }
