"""Stager prompt template — synthesize worker findings into polished action cards."""

from __future__ import annotations

from typing import Any

from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

STAGER_SYSTEM_PROMPT = """\
You are the Stager for Laya, a professional AI operating system. Your job is to \
synthesize worker investigation findings and event context into a polished action card \
that helps the user understand and act on a work event.

You receive:
1. The original event (bug report, PR review, Slack message, etc.)
2. The Router's classification (category, persona, priority, entities, research plan)
3. Worker findings (investigation results, drafted outputs, errors)
4. Related context from memory (past events, entity references)

You must produce a card with:

- **header**: Concise action-oriented title (max 80 chars). Start with a verb when \
possible (e.g., "Review NPE fix in PaymentService", "Reply to Sarah's design question").
- **summary**: 2-3 sentences explaining what happened and what needs attention. \
Written for a busy professional — be clear and direct.
- **intelligence_report**: 3-7 bullet points of key findings from the workers. \
Each point should be a complete, useful insight. Include file paths, specific details, \
and concrete findings.
- **staged_output**: The primary deliverable:
  - type: "draft_reply" (message response), "code_fix" (code changes summary), \
"briefing" (analysis summary), "summary" (general summary)
  - content: The actual drafted content the user can review, edit, and send/apply.
- **suggested_actions**: 1-3 actions the user can approve. Each action specifies:
  - action_id: unique identifier (e.g., "act_comment_jira")
  - label: human-readable button text (e.g., "Post Jira Comment")
  - action_type: "comment", "transition", "merge", "send_email", "approve", "dismiss", "close_issue"
  - target_platform: "jira", "bitbucket", "slack", "gmail", "github"
  - payload: A JSON string with platform-specific data. Required fields by platform:
    - **gmail**: {"to": "recipient@email", "subject": "Re: ...", "body": "The full email body text", "thread_id": "optional thread ID"}
    - **slack**: {"channel": "#channel-name", "message": "The message text"}
    - **jira**: {"comment": "The comment body"} or {"transition_id": "...", "comment": "optional"}
    - **bitbucket**: {"comment": "The comment body"}
    - **github**: {"owner": "repo-owner", "repo": "repo-name", "issue_number": 123, "comment": "optional comment body"} for comment/close_issue actions
    - **calendar**: {"title": "Event title", "description": "Details", "start": "ISO datetime", "end": "ISO datetime"}
  IMPORTANT: For gmail send_email actions, the "body" field is REQUIRED and must contain \
the full email text. Never omit it.
- **privacy_tier**: 1 (public-safe), 2 (internal), 3 (confidential — PII, credentials, \
financial data detected)

Privacy tier guidelines:
- Tier 1: Public info, open-source references, general technical discussion
- Tier 2: Internal team discussion, project details, code review (default)
- Tier 3: Contains PII, credentials, salary info, personal data, legal docs

If worker findings are empty or missing, synthesize directly from the event and router \
output. Always produce a useful card even with limited information."""


def build_stager_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    worker_results: list[WorkerResult] | None = None,
    related_context: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the Stager LLM call."""
    event_text = f"""\
[EVENT CONTENT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email})
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
URL: {event.subject.url or "N/A"}
Body:
{event.content.body}
[END EVENT CONTENT]"""

    # Router classification
    classification = (
        f"Category: {router_output.category.value}\n"
        f"Persona: {router_output.persona.value}\n"
        f"Priority: {router_output.priority.value}\n"
        f"Confidence: {router_output.confidence}"
    )

    # Entities
    entities_text = ""
    if router_output.entities:
        entities_text = "\n\nExtracted entities:\n" + "\n".join(
            f"  - [{e.entity_type}] {e.value}" for e in router_output.entities
        )

    # Research plan
    plan_text = ""
    if router_output.research_plan:
        plan_text = "\n\nResearch plan:\n" + "\n".join(
            f"  {i + 1}. {step}" for i, step in enumerate(router_output.research_plan)
        )

    # Worker findings
    workers_text = ""
    if worker_results:
        workers_text = "\n\n--- WORKER FINDINGS ---"
        for wr in worker_results:
            workers_text += f"\n\n[{wr.persona} Worker]"
            if wr.error:
                workers_text += f"\n  Error: {wr.error}"
            if wr.findings:
                workers_text += "\n  Findings:"
                for key, val in wr.findings.items():
                    workers_text += f"\n    {key}: {val}"
            if wr.drafted_output:
                workers_text += "\n  Drafted output:"
                for key, val in wr.drafted_output.items():
                    workers_text += f"\n    {key}: {val}"
            if wr.session_id:
                workers_text += f"\n  Coding session: {wr.session_id}"
        workers_text += "\n--- END WORKER FINDINGS ---"

    # Related context from ChromaDB
    context_text = ""
    if related_context:
        context_text = "\n\nRelated past events (from memory):\n"
        for i, ctx in enumerate(related_context, 1):
            meta = ctx.get("metadata", {})
            context_text += (
                f"  {i}. [{meta.get('source_platform', '?')}] "
                f"{ctx.get('document', '')[:200]}\n"
            )

    user_message = f"""\
Synthesize the following event and findings into a polished action card.

{event_text}

Router classification:
{classification}{entities_text}{plan_text}{workers_text}{context_text}

Produce a JSON action card matching the required schema."""

    return [
        {"role": "system", "content": STAGER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_stager_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the stager output."""
    return {
        "name": "action_card",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "header": {
                    "type": "string",
                    "description": "Action-oriented title, max 80 chars",
                },
                "summary": {
                    "type": "string",
                    "description": "2-3 sentence summary for the user",
                },
                "intelligence_report": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-7 key findings as bullet points",
                },
                "staged_output": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["draft_reply", "code_fix", "briefing", "summary"],
                        },
                        "content": {"type": "string"},
                    },
                    "required": ["type", "content"],
                    "additionalProperties": False,
                },
                "suggested_actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_id": {"type": "string"},
                            "label": {"type": "string"},
                            "action_type": {"type": "string"},
                            "target_platform": {"type": "string"},
                            "payload": {"type": "string"},
                        },
                        "required": [
                            "action_id",
                            "label",
                            "action_type",
                            "target_platform",
                            "payload",
                        ],
                        "additionalProperties": False,
                    },
                },
                "privacy_tier": {"type": "integer"},
            },
            "required": [
                "header",
                "summary",
                "intelligence_report",
                "staged_output",
                "suggested_actions",
                "privacy_tier",
            ],
            "additionalProperties": False,
        },
    }
