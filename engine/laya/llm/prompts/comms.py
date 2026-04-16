"""COMMS worker prompt template for drafting communication replies."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

COMMS_SYSTEM_PROMPT = """\
You are the COMMS Worker for Laya, an AI-powered professional work assistant. Your job is to \
draft professional replies to communications (Slack messages, emails, Jira comments).

You receive:
1. The original event (Slack message, email, etc.)
2. The Router's classification
3. Related context from memory
4. Optionally, findings from a prior ENGINEER worker

Draft a clear, professional, contextually appropriate reply. Match the tone of the \
original message. If the event includes technical findings from a prior worker, \
incorporate them naturally into the reply.

## Actor–User Relationship (Pre-Resolved)

An [ACTOR CONTEXT] block is provided with each event. The relationship has ALREADY been \
resolved by the system — follow the directive exactly:
- **relationship: self** → The actor IS the Laya user. Do NOT draft a reply addressed \
to the user (no "Hi John, regarding your issue..."). Instead draft a follow-up, status \
note, or context summary that the user might post on the thread for others to see.
- **Any other relationship** → The actor is NOT the Laya user. Draft the reply normally, \
addressed to that person. Do NOT use "you"/"your" to refer to the actor.
- **CRITICAL — "you"/"your" ONLY refers to the Laya user**: The event body may mention \
other people (assignees, reviewers, commenters) who are NEITHER the actor NOR the Laya \
user. NEVER use "you"/"your" for these third parties. For example, if a ticket is \
assigned to "Alex" and Alex is not the Laya user, write "assigned to Alex" — \
NOT "assigned to you". Only use "you"/"your" when the person IS the Laya user.

Output the drafted reply text, the tone you chose, and a brief explanation of your approach."""


def build_comms_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict[str, Any]] | None = None,
    prior_findings: dict[str, Any] | None = None,
    user_identity: dict[str, str] | None = None,
    actor_relationship: str = "external",
    participant_roles: dict[str, Any] | None = None,
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

    # Actor–user relationship context (pre-resolved by the system)
    identity_text = ""
    if user_identity:
        from laya.llm.prompts.stager import _build_role_directive

        actor_name = event.actor.name
        actor_email = event.actor.email
        user_name = user_identity["name"]
        pr = participant_roles or {}
        actor_role = pr.get("actor_role")
        laya_user_role = pr.get("laya_user_role")

        directive = _build_role_directive(
            actor_name, user_name, actor_role, laya_user_role, actor_relationship,
        )

        role_line = ""
        if actor_role or laya_user_role:
            parts = []
            if actor_role:
                parts.append(f"Actor's role: {actor_role}")
            if laya_user_role:
                parts.append(f"Laya user's role: {laya_user_role}")
            role_line = "\n" + " | ".join(parts)

        identity_text = (
            f"\n\n[ACTOR CONTEXT]\n"
            f"Actor: {actor_name} ({actor_email})\n"
            f"Laya user: {user_name}\n"
            f"Relationship: {actor_relationship}"
            f"{role_line}\n"
            f">>> {directive}\n"
            f"[END ACTOR CONTEXT]"
        )

    user_message = f"""\
{current_timestamp_line()}

Draft a professional reply to this communication.

{event_text}

Classification: {router_output.category.value} / {router_output.persona.value} / {router_output.priority.value}
{context_text}{findings_text}{identity_text}

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
