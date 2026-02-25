"""Router prompt template for event classification."""

from __future__ import annotations

from typing import Any

from laya.models.event import LayaEvent

ROUTER_SYSTEM_PROMPT = """\
You are the Router for Laya, a professional AI operating system. Your job is to \
classify incoming work events and plan investigation steps.

You receive a normalized event from the user's professional tools (Jira, Bitbucket, \
Slack, Gmail, Calendar) and must:

1. CLASSIFY the event:
   - category: CODE (code-related), COMMS (communication), PEOPLE (team/people), \
FINANCE (financial), OPS (operations/logistics)
   - persona: ENGINEER (code/technical), COMMS (communication/messaging), \
OPS (scheduling/operations)
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
code fix AND a Jira comment -> primary ENGINEER, secondary COMMS). null if not needed.

5. PROVIDE brief reasoning for your classification.

Priority guidelines:
- CRITICAL: Production issues, security alerts, blocking bugs, urgent messages from managers
- HIGH: Bug reports, PR review requests, direct messages needing response
- MEDIUM: General tickets, non-urgent mentions, FYI emails
- LOW: Bot notifications, status changes, informational updates

Actor relationship context (from team.json):
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
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
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
        "schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["CODE", "COMMS", "PEOPLE", "FINANCE", "OPS"],
                },
                "persona": {
                    "type": "string",
                    "enum": ["ENGINEER", "COMMS", "OPS"],
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
                            "platform": {"type": ["string", "null"]},
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
                    "type": ["string", "null"],
                    "enum": ["ENGINEER", "COMMS", "OPS", None],
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
        },
    }
