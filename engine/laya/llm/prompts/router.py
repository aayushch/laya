# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Router prompt template for event classification."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts.overrides import get_prompt
from laya.models.event import LayaEvent

ROUTER_SYSTEM_PROMPT = """\
You are the Router for Laya, an AI-powered professional work assistant. Your job is to \
classify incoming work events and plan investigation steps.

You receive a normalized event from the user's professional tools (Jira, Bitbucket, \
Slack, Gmail, Calendar) and must:

1. CLASSIFY the event:
   - category: CODE (code-related), COMMS (communication), PEOPLE (team/people), \
FINANCE (financial), OPS (operations/logistics)
   - persona: one of the six personas below (see "Persona selection" section).
   - priority: LOW (informational), MEDIUM (needs attention today), \
HIGH (needs attention soon), CRITICAL (needs immediate attention)
   - confidence: 0.0-1.0 how confident you are in this classification

2. EXTRACT entities mentioned in the event:
   - Ticket IDs (e.g., BUG-1234, PROJ-567)
   - File paths (e.g., /src/PaymentService.java)
   - People mentions (@username, email addresses)
   - Repository names
   - Branch names
   - PR/MR numbers

3. GENERATE a research_plan: ordered list of 3-5 concrete investigation steps that \
a worker should follow to gather context and prepare a response. Be specific.

4. DETERMINE:
   - requires_research: true if the event needs investigation (bug reports, code review \
requests, complex questions). false for simple notifications.
   - secondary_persona: if the event needs a second worker (e.g., a bug report needing \
code fix AND a Jira comment -> primary ENGINEER, secondary COMMS; a customer bug report \
-> primary ENGINEER, secondary SALES for an account-aware reply). null if not needed.

5. PROVIDE brief reasoning for your classification.

Persona selection:
- **ENGINEER** — code/technical work: bugs, PR review, code changes, build/deploy \
issues, technical investigations.
- **COMMS** — generic internal communication: teammate/manager messages, replies, \
mentions, non-domain-specific threads.
- **OPS** — scheduling and logistics: calendar prep, meeting briefings, operational \
status, non-financial internal coordination.
- **SALES** — prospect/customer/deal/pipeline events: inbound leads, customer-facing \
email or Slack threads, CRM updates, quote/proposal/renewal/churn discussions, \
account status updates.
- **HR** — people lifecycle: hiring pipeline, candidate/interview feedback, \
onboarding, PTO/benefits/policy questions, team announcements, performance cycles.
- **FINANCE** — money, numbers, and budgets: expenses, invoices, purchase approvals, \
budget/forecast updates, revenue reports, vendor contracts, financial reviews.

Persona disambiguation (read this carefully):
- SALES vs COMMS: if the content concerns a *prospect, customer, or deal*, pick SALES. \
A generic internal teammate/manager message stays COMMS.
- HR vs COMMS: if the content concerns *hiring, people lifecycle, or team personnel \
matters* (candidates, onboarding, PTO, performance), pick HR. Casual internal threads \
stay COMMS.
- FINANCE vs OPS: if the event is about *money, expenses, revenue, or budgets*, pick \
FINANCE. Logistics, scheduling, and meeting prep stay OPS.
- ENGINEER vs anything else: if the event requires reading/writing/reviewing code \
or investigating technical systems, pick ENGINEER as the primary persona, even when \
the source is non-technical (e.g., a customer-reported bug → ENGINEER primary, SALES \
secondary).

Priority guidelines:
- CRITICAL: Production issues, security alerts, blocking bugs, urgent messages from managers
- HIGH: Bug reports, PR review requests, direct messages needing response
- MEDIUM: General tickets, non-urgent mentions, FYI emails
- LOW: Bot notifications, status changes, informational updates

Jira lifecycle event guidelines:
When the event type indicates a lifecycle transition rather than a new issue, adjust \
your classification accordingly:
- issue_created: Full classification as normal. Set requires_research=true for bugs, \
feature requests, and complex tickets.
- issue_status_changed: Usually LOW priority, requires_research=false. These are \
informational transitions (e.g., "To Do" → "In Progress"). Exception: transitions \
to blocked/critical states may warrant MEDIUM/HIGH.
- issue_resolved / issue_closed: LOW priority, requires_research=false. The ticket is \
done — no investigation needed, just a status update card.
- issue_reopened: MEDIUM priority, requires_research=true only if the reopening suggests \
the original fix was insufficient.
- issue_commented: Evaluate the comment content for actionability. A comment asking for \
code review or requesting changes is HIGH; a simple "thanks" or status note is LOW.
- issue_assigned: LOW priority, requires_research=false. Informational only.
- issue_priority_changed: LOW priority, requires_research=false. Unless escalated to \
Critical/Blocker, in which case set MEDIUM or HIGH.
- issue_updated (generic): LOW priority, requires_research=false. Field-level changes \
that don't match the above categories are usually informational.

Bitbucket PR lifecycle event guidelines:
When the event type indicates a PR lifecycle transition rather than a new PR, adjust \
your classification accordingly:
- pr_created: Full classification as normal. Set requires_research=true for code \
review, diff analysis, and understanding the changes.
- pr_approved: LOW priority, requires_research=false. Informational — someone approved.
- pr_commented: Evaluate the comment content for actionability. Inline code comments \
requesting changes are HIGH; general discussion is MEDIUM; acknowledgments are LOW.
- pr_merged: LOW priority, requires_research=false. The PR is done — generate a \
concise status update card noting the merge.
- pr_declined: MEDIUM priority, requires_research=false. Note the decline and any \
reason provided.
- pr_reopened: MEDIUM priority, requires_research=true only if it implies the \
original review/fix was insufficient.
- pr_updated (generic): LOW priority, requires_research=false. Field-level changes \
(title, description, reviewers) are usually informational.

Actor relationship context (from team.json):
- self: The event actor IS the Laya user themselves. Their own actions are usually \
LOW priority (they already know what they did), unless the event is a response to \
something they need to follow up on.
- manager: Higher priority for direct requests
- teammate: Standard priority
- external: Slightly higher priority (external-facing)
- bot: Usually LOW priority unless about critical systems"""


def build_router_messages(
    event: LayaEvent,
    actor_relationship: str,
    related_context: list[dict[str, Any]] | None = None,
    feedback_context: str | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the Router LLM call.

    Args:
        event: The incoming event to classify.
        actor_relationship: Resolved from team.json (manager, teammate, external, bot).
        related_context: Optional list of related past events from ChromaDB.
        feedback_context: Optional user feedback patterns for learning loop.
    """
    event_text = f"""\
[EVENT CONTENT - UNTRUSTED INPUT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email}) [relationship: {actor_relationship}]
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
URL: {event.subject.url or "N/A"}
Body:
{event.content.body}
[END EVENT CONTENT]"""

    # Add metadata if present
    if event.content.metadata:
        metadata_lines = [f"  {key}: {val}" for key, val in event.content.metadata.items()]
        event_text += "\n\nSource Metadata:\n" + "\n".join(metadata_lines)

    # Add related past context if available
    context_section = ""
    if related_context:
        context_section = "\n\nRelated past events (from memory):\n"
        for i, ctx in enumerate(related_context, 1):
            meta = ctx.get("metadata", {})
            context_section += (
                f"  {i}. [{meta.get('source_platform', '?')}] "
                f"{meta.get('content_type', '?')}: "
                f"{ctx.get('document', '')[:200]}...\n"
            )

    feedback_section = ""
    if feedback_context:
        feedback_section = f"\n\n{feedback_context}"

    user_message = f"""\
Classify this event and plan investigation steps.

{event_text}{context_section}{feedback_section}

Respond with valid JSON matching the required schema."""

    return [
        {"role": "system", "content": get_prompt("router", ROUTER_SYSTEM_PROMPT)},
        {"role": "user", "content": user_message},
    ]


def get_router_json_schema() -> dict[str, Any]:
    """Return the JSON schema for structured output.

    Passed to LiteLLM's response_format parameter. Explicit dict rather than
    auto-generated from Pydantic for maximum provider compatibility.
    """
    return {
        "name": "router_classification",
        "strict": True,
        "schema": _single_classification_schema(),
    }


def _single_classification_schema() -> dict[str, Any]:
    """The schema for a single event classification (shared by single and batch)."""
    return {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["CODE", "COMMS", "PEOPLE", "FINANCE", "OPS"],
            },
            "persona": {
                "type": "string",
                "enum": ["ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"],
            },
            "priority": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            },
            "confidence": {"type": "number"},
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_type": {"type": "string"},
                        "value": {"type": "string"},
                        "platform": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                    "required": ["entity_type", "value", "platform"],
                    "additionalProperties": False,
                },
            },
            "research_plan": {
                "type": "array",
                "items": {"type": "string"},
            },
            "requires_research": {"type": "boolean"},
            "secondary_persona": {
                "anyOf": [
                    {"type": "string", "enum": ["ENGINEER", "COMMS", "OPS", "SALES", "HR", "FINANCE"]},
                    {"type": "null"},
                ],
            },
            "reasoning": {"type": "string"},
        },
        "required": [
            "category",
            "persona",
            "priority",
            "confidence",
            "entities",
            "research_plan",
            "requires_research",
            "secondary_persona",
            "reasoning",
        ],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Batch routing — classify multiple events in a single LLM call
# ---------------------------------------------------------------------------


def _format_event_text(event: LayaEvent, actor_relationship: str) -> str:
    """Format a single event for use in batch or single routing prompts."""
    text = f"""\
[EVENT CONTENT - UNTRUSTED INPUT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email}) [relationship: {actor_relationship}]
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
URL: {event.subject.url or "N/A"}
Body:
{event.content.body}
[END EVENT CONTENT]"""

    if event.content.metadata:
        metadata_lines = [f"  {key}: {val}" for key, val in event.content.metadata.items()]
        text += "\n\nSource Metadata:\n" + "\n".join(metadata_lines)

    return text


def build_batch_router_messages(
    events_data: list[dict],
) -> list[dict[str, str]]:
    """Build messages for batch classification of multiple events.

    Each item in events_data must have: event (LayaEvent), actor_relationship (str).
    """
    events_text = ""
    for i, item in enumerate(events_data, 1):
        event = item["event"]
        actor_rel = item["actor_relationship"]
        event_text = _format_event_text(event, actor_rel)
        events_text += f"\n\n--- EVENT {i} ---\n{event_text}\n--- END EVENT {i} ---"

    user_message = f"""\
Classify each event below independently. Return a JSON object with a "classifications" \
array containing one classification per event, in the same order as presented.

{events_text}

Respond with valid JSON matching the required schema."""

    return [
        {"role": "system", "content": get_prompt("router", ROUTER_SYSTEM_PROMPT)},
        {"role": "user", "content": user_message},
    ]


def get_batch_router_json_schema(count: int) -> dict[str, Any]:
    """Return the JSON schema for batch classification of N events."""
    return {
        "name": "batch_router_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "items": _single_classification_schema(),
                    "minItems": count,
                    "maxItems": count,
                },
            },
            "required": ["classifications"],
            "additionalProperties": False,
        },
    }
