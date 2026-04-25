"""ENGINEER worker prompt template for building coding agent task descriptions."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent

ENGINEER_SYSTEM_PROMPT = """\
You are the ENGINEER Worker for Laya, an AI-powered professional work assistant. Your job is to \
prepare a detailed task prompt for a coding agent that will investigate and fix code issues.

You receive:
1. The original event (bug report, PR review request, etc.)
2. The Router's classification and research plan
3. Related context from memory (past events, entity references)

You must produce a clear, detailed task prompt that a coding agent (Claude Code, Gemini CLI, \
or Codex CLI) can execute in the target repository. The prompt should:

- State the problem clearly
- Reference specific files, classes, and methods mentioned in the event
- Include the research plan steps as investigation instructions
- Include related context from past events
- Specify what the expected output is (investigation findings, a fix, a review summary)

Be specific and actionable. The coding agent will work in the repository directory.
Never use emoji or icon characters in your output."""


def build_engineer_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    related_context: list[dict[str, Any]] | None = None,
    entity_context: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the ENGINEER worker LLM call."""
    event_text = f"""\
[EVENT CONTENT - UNTRUSTED INPUT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email})
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
URL: {event.subject.url or "N/A"}
Body:
{event.content.body}
[END EVENT CONTENT]"""

    research_plan = "\n".join(
        f"  {i + 1}. {step}" for i, step in enumerate(router_output.research_plan)
    )

    entities_text = ""
    if router_output.entities:
        entities_text = "\n\nExtracted entities:\n" + "\n".join(
            f"  - [{e.entity_type}] {e.value}" for e in router_output.entities
        )

    context_text = ""
    if related_context:
        context_text = "\n\nRelated past events (from memory):\n"
        for i, ctx in enumerate(related_context, 1):
            meta = ctx.get("metadata", {})
            context_text += (
                f"  {i}. [{meta.get('source_platform', '?')}] "
                f"{ctx.get('document', '')[:200]}\n"
            )

    entity_ctx_text = ""
    if entity_context:
        entity_ctx_text = "\n\nKnown entities:\n"
        for ent in entity_context:
            entity_ctx_text += f"  - {ent.get('canonical_name', '?')} ({ent.get('entity_type', '?')})\n"

    user_message = f"""\
{current_timestamp_line()}

Build a task prompt for a coding agent to investigate this issue.

{event_text}

Classification: {router_output.category.value} / {router_output.persona.value} / {router_output.priority.value}

Research plan from Router:
{research_plan}
{entities_text}{context_text}{entity_ctx_text}

Produce a clear, detailed prompt that the coding agent can execute in the repository."""

    return [
        {"role": "system", "content": ENGINEER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_engineer_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the engineer worker output."""
    return {
        "name": "engineer_task_prompt",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "task_prompt": {
                    "type": "string",
                    "description": "The detailed prompt for the coding agent",
                },
                "target_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files the agent should focus on",
                },
                "expected_output": {
                    "type": "string",
                    "description": "What the agent should produce",
                },
            },
            "required": ["task_prompt", "target_files", "expected_output"],
            "additionalProperties": False,
        },
    }
