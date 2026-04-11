"""Stager prompt template — synthesize worker findings into polished action cards."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

STAGER_SYSTEM_PROMPT = """\
You are the Stager for Laya, an AI-powered professional work assistant. Your job is to \
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
  - action_type: must match the target_platform. Valid combinations:
    - **gmail/outlook**: "send_email", "forward", "archive", "mark_read"
    - **slack**: "send_message", "reply_thread"
    - **jira/linear**: "comment", "transition", "create_issue", "assign"
    - **bitbucket**: "comment_pr", "approve_pr", "request_changes", "merge_pr", "decline_pr", "create_pr"
    - **github**: "comment", "close_issue", "approve_pr", "merge_pr", "request_changes", "create_issue"
    - **google_calendar**: "calendar"
    IMPORTANT: For email replies on gmail/outlook, always use "send_email" — NOT "send_message". \
"send_message" is ONLY for Slack.
  - target_platform: "jira", "bitbucket", "slack", "gmail", "github", "google_calendar", \
"outlook", "linear"
  - payload: A JSON string with platform-specific data. Required fields by platform:
    - **gmail**: {"to": "sender@email (the person who SENT the original email, i.e. the actor — NOT the user's own email)", "subject": "Re: ...", "body": "The full email body text", "thread_id": "optional thread ID", "cc": "optional"}
    - **outlook**: {"to": "sender@email (the original sender/actor — NOT the user)", "subject": "Re: ...", "body": "email text", "conversation_id": "optional"} \
for send_email/forward; {"outlook_id": "the outlook message ID from the event"} for archive/mark_read
    - **slack**: {"channel": "channel-name-or-id", "message": "The message text"} \
for send_message; add "thread_ts": "timestamp" for reply_thread
    - **jira**: {"issue_key": "PROJ-123", "comment": "The comment body"} for comment; \
{"issue_key": "PROJ-123", "target_status": "Done"} for transition; \
{"project": "PROJ", "summary": "Title", "description": "...", "type": "Task"} for create_issue
    - **bitbucket**: {"workspace": "ws", "repo": "repo", "pr_id": "123", "comment": "body", "comment_id": "optional — the ID of the comment to reply to (from bb_comment_id in event metadata). Include this when replying to a specific comment thread so the reply is posted as a thread reply, not a new top-level comment."} \
for comment_pr; same without comment for approve_pr, decline_pr, merge_pr
    - **github**: {"owner": "repo-owner", "repo": "repo-name", "issue_number": 123, "comment": "body"} \
for comment/close_issue; {"owner": "o", "repo": "r", "pr_number": 123} for approve_pr/merge_pr/request_changes; \
{"owner": "o", "repo": "r", "title": "...", "body": "..."} for create_issue
    - **linear**: {"issue_id": "...", "body": "comment"} for comment; \
{"team_id": "...", "title": "...", "description": "..."} for create_issue
    - **google_calendar**: {"title": "Event title", "description": "Details", "start": "ISO datetime", "end": "ISO datetime"}
  IMPORTANT: For gmail send_email actions, the "body" field is REQUIRED and must contain \
the full email text. Never omit it.
- **privacy_tier**: 1 (public-safe), 2 (internal), 3 (confidential — PII, credentials, \
financial data detected)

Privacy tier guidelines:
- Tier 1: Public info, open-source references, general technical discussion
- Tier 2: Internal team discussion, project details, code review (default)
- Tier 3: Contains PII, credentials, salary info, personal data, legal docs

If worker findings are empty or missing, synthesize directly from the event and router \
output. Always produce a useful card even with limited information.

## Update Events vs New Events

When existing cards are listed for the same entity, you are generating an UPDATE card, \
not a first-time investigation. Follow these rules:

- **Status changes** (issue_status_changed, issue_resolved, issue_assigned, \
issue_priority_changed, issue_reopened): Generate a brief status update card. The header \
should reflect the change (e.g., "BUG-1234 resolved as Fixed"), not re-describe the \
original issue. Summary should be 1-2 sentences. Intelligence report should be 1-3 \
bullets max. Do NOT repeat research or analysis from existing cards.

- **Closure/Resolution** (issue_resolved): Summarize the outcome. If a resolution \
comment or details exist, include them. Suggest relevant follow-up actions (e.g., \
"Close related PR", "Update documentation", "Verify fix in staging").

- **Comments** (issue_commented): Focus on the new comment content and what action it \
requires. Do not re-summarize the original ticket.

- **Reopened** (issue_reopened): Note the ticket was reopened and what the new status is. \
Suggest investigation if the reopening implies the original fix was insufficient.

- **New issues** (issue_created, or no existing cards): Full investigation as normal.

### Bitbucket PR lifecycle:
- **Approvals** (pr_approved): Brief card noting who approved. 1-2 sentences max.
- **Comments** (pr_commented): Focus on the comment content. Inline code comments \
should reference the file and line. Do not re-summarize the PR description.
- **Merged** (pr_merged): Concise closure card noting the merge. Include merge commit \
if available. Suggest follow-up actions (e.g., "Delete source branch", "Deploy to staging").
- **Declined** (pr_declined): Note the decline and reason if provided.
- **Reopened** (pr_reopened): Note the reopen and suggest re-review if needed.
- **New PRs** (pr_created, or no existing cards): Full investigation as normal.

For update cards, the staged_output type should be "status_update" unless the update \
contains substantive new content requiring action (e.g., a comment requesting code review \
should still use "code_fix" or "draft_reply").

## Actor–User Relationship (Pre-Resolved)

An [ACTOR CONTEXT] block is provided with each event. The relationship between the event \
actor and the Laya user has ALREADY been resolved by the system — do NOT attempt your own \
comparison. Follow the directive exactly:

- **relationship: self** → The actor IS the Laya user. Use first-person framing in \
headers and summaries — "You opened issue BUG-123", "Your PR #45 was merged". \
NEVER draft a reply addressed to the user themselves. The staged_output should be a \
brief summary or status update, not a self-addressed message. Suggested actions are \
still valid (the user may want to comment, transition, or close their own items) — \
just ensure payload content is not addressed to themselves.
- **relationship: manager / teammate / external / bot** → The actor is NOT the Laya \
user. Use third-person framing — "{actor_name} opened issue BUG-123", \
"{actor_name}'s PR #45". Draft replies addressed to the actor. Do NOT use "you" or \
"your" to refer to the actor — those pronouns refer ONLY to the Laya user.
- **Actions from others on user's items**: If someone else acts on the user's item \
(e.g., Jane comments on a PR the user created), frame it as "Jane commented on your \
PR #45" and the pre-drafted response should address Jane, not the user.
- **If no [ACTOR CONTEXT] is provided**, fall back to third-person behavior."""


def build_stager_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    worker_results: list[WorkerResult] | None = None,
    related_context: list[dict[str, Any]] | None = None,
    entity_history: list[dict[str, Any]] | None = None,
    user_identity: dict[str, str] | None = None,
    actor_relationship: str = "external",
) -> list[dict[str, str]]:
    """Build the messages array for the Stager LLM call."""
    # Include event metadata so the LLM can use platform-specific IDs
    # (e.g., bb_comment_id for Bitbucket thread replies, gmail_thread_id for Gmail)
    metadata_text = ""
    if event.content.metadata:
        metadata_lines = []
        for k, v in event.content.metadata.items():
            if v is not None and v != "" and v != []:
                metadata_lines.append(f"  {k}: {v}")
        if metadata_lines:
            metadata_text = "\nMetadata:\n" + "\n".join(metadata_lines)

    event_text = f"""\
[EVENT CONTENT]
Platform: {event.source.platform}
Event Type: {event.source.raw_event_type}
Actor: {event.actor.name} ({event.actor.email})
Subject: [{event.subject.type}] {event.subject.id} — {event.subject.title}
URL: {event.subject.url or "N/A"}
Body:
{event.content.body}{metadata_text}
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

    # Existing cards for this entity (prevents redundant research)
    entity_text = ""
    if entity_history:
        entity_text = (
            f"\n\n[EXISTING CARDS FOR THIS ENTITY]\n"
            f"This entity already has {len(entity_history)} card(s). "
            f"DO NOT repeat research or analysis already covered:\n"
        )
        for i, card in enumerate(entity_history, 1):
            entity_text += (
                f"  {i}. [{card.get('created_at', '?')}] "
                f"\"{card.get('header', '?')}\" "
                f"({card.get('status', '?')}) — "
                f"from {card.get('source_raw_event_type', '?')}\n"
            )
        entity_text += (
            "For status updates, generate a concise update card — "
            "not a new investigation.\n"
            "[END EXISTING CARDS]"
        )

    # Actor–user relationship context (pre-resolved by the system)
    identity_text = ""
    if user_identity:
        actor_name = event.actor.name
        actor_email = event.actor.email
        user_name = user_identity["name"]
        is_self = actor_relationship == "self"
        if is_self:
            directive = (
                f"The actor IS the Laya user ({user_name}). "
                "Use first-person framing (\"You opened…\", \"Your PR…\"). "
                "Do NOT draft replies addressed to yourself."
            )
        else:
            directive = (
                f"The actor ({actor_name}) is NOT the Laya user ({user_name}). "
                f"Use third-person framing (\"{actor_name} opened…\"). "
                f"Do NOT attribute this action to the user."
            )
        identity_text = (
            f"\n\n[ACTOR CONTEXT]\n"
            f"Actor: {actor_name} ({actor_email})\n"
            f"Laya user: {user_name}\n"
            f"Relationship: {actor_relationship}\n"
            f">>> {directive}\n"
            f"[END ACTOR CONTEXT]"
        )

    user_message = f"""\
{current_timestamp_line()}

Synthesize the following event and findings into a polished action card.

{event_text}

Router classification:
{classification}{entities_text}{plan_text}{workers_text}{context_text}{entity_text}{identity_text}

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
                            "enum": ["draft_reply", "code_fix", "briefing", "summary", "status_update"],
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
                "privacy_tier": {"type": "integer", "minimum": 1, "maximum": 3},
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
