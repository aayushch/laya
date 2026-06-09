# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Stager prompt template — synthesize worker findings into polished action cards."""

from __future__ import annotations

from typing import Any

from laya.egress.registry import get_capabilities
from laya.llm.prompts.overrides import get_prompt
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult


def format_supported_actions(platform: str) -> str:
    """Render the registry's capabilities for ``platform`` as a markdown
    [SUPPORTED ACTIONS] block for injection into the stager user message.

    Shows each capability's ``content_fields`` / ``optional_content_fields``
    directly — the registry declares which fields the LLM must emit, so no
    external filter list is needed.  Identifier fields are absent by
    construction because capabilities don't list them as content.
    """
    caps = get_capabilities(platform)
    if not caps:
        return ""

    lines = [
        "[SUPPORTED ACTIONS]",
        (
            "Pick `action_type` ONLY from this list. The engine fills "
            "identifier fields automatically from the source event — emit "
            "only the content fields listed."
        ),
    ]
    for cap in caps:
        field_parts = []
        if cap.content_fields:
            field_parts.append(f"required content fields: {', '.join(cap.content_fields)}")
        if cap.optional_content_fields:
            field_parts.append(f"optional: {', '.join(cap.optional_content_fields)}")
        fields_text = f" — {'; '.join(field_parts)}" if field_parts else ""
        lines.append(
            f'- action_type="{cap.action_type}" (label "{cap.label}"): '
            f"{cap.description}{fields_text}"
        )
    lines.append("[END SUPPORTED ACTIONS]")
    return "\n".join(lines)

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

- **header**: Concise title (max 80 chars). When the Laya user has an assigned role \
on the item, use an action verb ("Review NPE fix in PaymentService", "Reply to Sarah's \
design question"). When the user has no assigned role and is monitoring, use a descriptive \
title ("Sarah opened PR: NPE fix in PaymentService", "PR #45 merged into main"). The \
[ACTOR CONTEXT] block determines which framing to use.
- **summary**: 2-3 sentences explaining what happened and what needs attention. \
Written for a busy professional — be clear and direct.
- **intelligence_report**: 3-7 bullet points of key findings from the workers. \
Each point should be a complete, useful insight. Include file paths, specific details, \
and concrete findings.
- **staged_output**: The primary deliverable:
  - type: "draft_reply" (message response), "code_fix" (code changes summary), \
"briefing" (analysis summary), "summary" (general summary)
  - content: The actual drafted content the user can review, edit, and send/apply.
- **suggested_actions**: 0-3 actions the user can approve. If the event is purely \
informational, a status update, or noise (e.g., polling with no changes), return an \
empty array — do not invent actions that have no meaningful effect. Each action specifies:
  - action_id: unique identifier (e.g., "act_comment_jira")
  - label: human-readable button text (e.g., "Post Jira Comment")
  - action_type: MUST be chosen from the [SUPPORTED ACTIONS] block in the user \
message below. That list is authoritative for this event's platform. Never \
invent an action_type that isn't listed — it will be rejected by the executor.
  - target_platform: MUST equal the event's source platform (shown under [EVENT CONTENT] as \
"Platform:"). Example: if Platform is "outlook", every suggested action must target "outlook" \
— never "gmail", "google_calendar", or any other platform. The ONLY exceptions are:
    (a) the event platform is "gmail" or "outlook" and the action forwards/replies from the \
user's other configured mail platform (still rare — default to the event's own platform);
    (b) the event platform has no outbound capability for the intended action.
    When in doubt, match the event platform. Do NOT "enrich" cards with suggestions that \
jump to unrelated platforms (e.g., never suggest a Google Calendar action on an Outlook \
payment confirmation, or a Slack action on a Jira ticket).
  - payload: A JSON object with CONTENT fields for the chosen action. The \
required/optional content fields for each action are listed in the \
[SUPPORTED ACTIONS] block in the user message. Do NOT emit identifier fields \
(owner, repo, issue_number, pr_number, issue_key, issue_id, team_id, \
workspace, pr_id, channel, thread_ts, gmail_id, outlook_id, \
conversation_id, thread_id, comment_id, timestamp, to) — they are filled \
automatically by the engine from the source event. Emit ONLY the content \
fields the action needs (e.g., comment body, email subject+body, issue \
title+description, transition target_status, merge_method).
## Actionable Links (open_url)

When the email body contains a URL that represents a clear user action — such as an \
unsubscribe link, a subscription confirmation link, an approval link, or a verification \
link — suggest an open_url action. Extract the EXACT URL from the email body and emit it \
as the "url" content field. Use a descriptive label that explains what the link does \
(e.g., "Unsubscribe", "Confirm Subscription", "View Invoice", "Approve Request"). \
Do NOT suggest open_url for generic marketing links, homepage links, social media \
profile links, or every hyperlink in the content — only for links that represent a \
specific action the user would want to take.

SECURITY — NEVER suggest open_url in any of these situations:
- The email shows signs of phishing or social engineering: sender domain mismatch, \
urgency/threat language ("your account will be suspended", "act now or lose access"), \
requests for credentials or personal information, impersonation of known brands with \
slight misspellings, or generic greetings ("Dear Customer", "Dear User").
- The link domain does not plausibly match the sender's organization (e.g., sender is \
newsletter@acme.com but the link goes to acme-login.suspicious-site.xyz).
- The URL looks obfuscated, uses URL shorteners from unfamiliar services, or contains \
suspicious patterns (IP addresses instead of domains, excessive subdomains, encoded \
characters in the domain).
When in doubt about the email's legitimacy, mention the link in the intelligence report \
ONLY — do not surface it as a suggested action. A missed unsubscribe button is harmless; \
a phishing link disguised as one is not. If the email appears to be spam or phishing, \
flag it in the intelligence report.

- **privacy_tier**: 1 (public-safe), 2 (internal), 3 (confidential — PII, credentials, \
financial data detected)

Privacy tier guidelines:
- Tier 1: Public info, open-source references, general technical discussion
- Tier 2: Internal team discussion, project details, code review (default)
- Tier 3: Contains PII, credentials, salary info, personal data, legal docs

If worker findings are empty or missing, synthesize directly from the event and router \
output. Always produce a useful card even with limited information.

Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) in any field — headers, \
summaries, intelligence bullets, or drafted content. Use plain text only.

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

### PR lifecycle (Bitbucket / GitHub):
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
user. Use third-person framing — "{{actor_name}} opened issue BUG-123", \
"{{actor_name}}'s PR #45". Draft replies addressed to the actor. Do NOT use "you" or \
"your" to refer to the actor — those pronouns refer ONLY to the Laya user.
- **Actions from others on user's items**: If someone else acts on the user's item \
(e.g., Jane comments on a PR the user created), frame it as "Jane commented on your \
PR #45" and the pre-drafted response should address Jane, not the user.
- **CRITICAL — "you"/"your" ONLY refers to the Laya user**: The event body may mention \
other people (assignees, reviewers, commenters) who are NEITHER the actor NOR the Laya \
user. NEVER use "you"/"your" for these third parties. For example, if a ticket is \
assigned to "Alex" and Alex is not the Laya user, write "assigned to Alex" — \
NOT "assigned to you". Only use "you"/"your" when the [ACTOR CONTEXT] confirms the \
person IS the Laya user (relationship: self), or when referring to items the Laya user \
owns (e.g., "your PR" when the Laya user created it).
- **If no [ACTOR CONTEXT] is provided**, fall back to third-person behavior.

## Participant Roles and User Involvement

The [ACTOR CONTEXT] block includes the Laya user's role on this specific work item \
(e.g., "reviewer", "author", "assignee") when one exists. These roles come from \
platform data — they are facts, not inferences. **Never infer or assume a role that \
the system has not explicitly assigned.**

Three framing modes based on the Laya user's role:

1. **User has an assigned role** (reviewer, author, assignee, etc.): Frame summaries, \
headers, and drafts from that role's perspective. Follow the ROLE CONTEXT directive in \
[ACTOR CONTEXT].

2. **User has NO assigned role** (no "Laya user's role" shown, or ROLE CONTEXT says \
"no assigned role"): The user is monitoring this item but is not a direct participant. \
Frame the card as an awareness update. Report what happened, who is involved, and any \
relevant details, but do NOT imply the user needs to act. Specifically:
   - Do NOT assume the user should review a PR because a PR event arrived — only a \
listed reviewer should be told to review.
   - Do NOT draft replies or feedback as if the user is a reviewer or assignee.
   - Use descriptive headers ("{{Actor}} opened PR: {{title}}") rather than action headers \
("Review {{title}}").
   - Limit suggested_actions to lightweight follow-ups (e.g., comment, bookmark) rather \
than ownership actions (approve, request changes, merge).
   The user benefits from awareness of project activity, not from being assigned work \
they were not asked to do.

3. **No [ACTOR CONTEXT] provided**: Fall back to informational framing (mode 2) — \
assume monitoring rather than participation.

In all modes:
- **Use the actor's role** to describe their actions accurately ("Author pushed a fix", \
"Reviewer approved").
- **A participant roster** may list all known participants. Use it to correctly reference \
people and their relationships rather than guessing.

## Tags

Suggest 0-3 short lowercase tag names (single words or hyphenated-phrases) that categorize \
this event. Good tags help users filter and organize their feed. Examples: "billing", \
"security", "deployment", "code-review", "onboarding", "incident", "compliance". \
Only suggest tags when they add genuine categorization value beyond what priority, persona, \
and category already capture. If existing tags are listed in [EXISTING TAGS], prefer reusing \
them when they fit — avoid synonyms like "deploy" vs "deployment". Return an empty array \
when no tags are warranted.

## Context Matching

{context_matching_directive}"""


_CONTEXT_MATCHING_DIRECTIVES = {
    "strict": (
        'Among the "Related past cards" listed in the input, identify ONLY if any card with '
        "a DIFFERENT entity references the EXACT SAME specific incident, ticket, transaction, "
        "or entity as this event. They must share concrete identifiers (ticket numbers, PR IDs, "
        "service names, order numbers). Two cards about the same TYPE of issue (e.g., two different "
        "NPE bugs in different services) are NOT the same issue.\n\n"
        "If you identify a match with high confidence, output the card_id in context_match. "
        "If no match or any uncertainty, output matched_card_id as null with an empty label."
    ),
    "balanced": (
        'Among the "Related past cards" listed in the input, identify if any card with a DIFFERENT '
        "entity than this event is about the same real-world context (same transaction, project, "
        "or situation across platforms).\n\n"
        "Same context examples: a bill notification + payment receipt for that bill, a PR comment + "
        "the CI build it triggered, a meeting invite + follow-up notes for that meeting, a shipping "
        "confirmation + delivery notification for the same order.\n\n"
        "NOT same context: two unrelated emails of the same type, two different newsletters, two "
        "alerts about different services, two reviews of different content. Notifications are NOT "
        "the same context just because they are the same notification type.\n\n"
        "If you identify a match, output the card_id in context_match. If no match or uncertain, "
        "output matched_card_id as null with an empty label."
    ),
    "lenient": (
        'Among the "Related past cards" listed in the input, identify if any card with a DIFFERENT '
        "entity could provide useful context for understanding this event. Consider same project "
        "area, related topics, overlapping people, or similar timeframe.\n\n"
        "If you identify a relationship that would help the user, output the card_id in "
        "context_match. If no meaningful relationship, output matched_card_id as null with an "
        "empty label."
    ),
}


def _build_role_directive(
    actor_name: str,
    user_name: str,
    actor_role: str | None,
    laya_user_role: str | None,
    actor_relationship: str,
) -> str:
    """Build a role-aware behavioral directive for the stager/comms prompts.

    Uses both the identity relationship (self/teammate/external) and the
    contextual roles (author/reviewer/assignee/etc.) to guide the LLM on
    how to frame summaries and draft replies.
    """
    is_self = actor_relationship == "self"

    if is_self:
        role_hint = f" (acting as {actor_role})" if actor_role else ""
        return (
            f"The actor IS the Laya user ({user_name}){role_hint}. "
            "Use first-person framing (\"You opened…\", \"Your PR…\"). "
            "Do NOT draft replies addressed to yourself."
        )

    # Actor is someone else — build directive based on role combination
    base = (
        f"The actor ({actor_name}) is NOT the Laya user ({user_name}). "
        f"Use third-person framing (\"{actor_name} opened…\"). "
        f"Do NOT attribute this action to the user. "
        f"\"You\"/\"your\" refers ONLY to the Laya user — never to any other person in the event."
    )

    # Add role-specific guidance when both roles are known
    role_guidance = ""
    if actor_role and laya_user_role:
        # PR scenarios
        if actor_role == "author" and laya_user_role == "reviewer":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} is the PR/item AUTHOR. "
                f"The Laya user ({user_name}) is a REVIEWER. "
                "When drafting replies, write from the reviewer's perspective — provide feedback, "
                "ask questions, approve, or request changes. Do NOT draft replies that commit the "
                "reviewer to doing the author's work (refactoring, fixing, implementing). "
                "The author owns the code changes."
            )
        elif actor_role == "reviewer" and laya_user_role == "author":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} is a REVIEWER. "
                f"The Laya user ({user_name}) is the AUTHOR. "
                "When drafting replies, write from the author's perspective — address review feedback, "
                "explain decisions, commit to action items on your own code."
            )
        elif actor_role == "commenter" and laya_user_role == "author":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} commented on the Laya user's item. "
                f"The Laya user ({user_name}) is the AUTHOR/OWNER. "
                "Draft replies that address the comment from the owner's perspective."
            )
        elif actor_role == "commenter" and laya_user_role == "reviewer":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} commented. "
                f"The Laya user ({user_name}) is a REVIEWER. "
                "Draft replies from the reviewer's perspective — provide input on the discussion."
            )
        # Issue/ticket scenarios
        elif actor_role in ("reporter", "creator") and laya_user_role == "assignee":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} is the REPORTER/CREATOR. "
                f"The Laya user ({user_name}) is the ASSIGNEE. "
                "This is assigned to the user — draft acknowledgments, ask clarifying questions, "
                "or provide status updates from the assignee's perspective."
            )
        elif actor_role == "assignee" and laya_user_role in ("reporter", "creator"):
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} is the ASSIGNEE. "
                f"The Laya user ({user_name}) is the REPORTER/CREATOR. "
                "Draft follow-ups or status checks from the reporter's perspective."
            )
        # Email scenarios
        elif actor_role == "sender" and laya_user_role == "recipient":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} sent this to the Laya user. "
                "Draft replies addressed to the sender."
            )
        # Calendar scenarios
        elif actor_role == "organizer" and laya_user_role == "attendee":
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name} organized this event. "
                f"The Laya user ({user_name}) is an attendee."
            )
        # Generic: roles are known but no specific combination matched
        else:
            role_guidance = (
                f"\n>>> ROLE CONTEXT: {actor_name}'s role: {actor_role}. "
                f"Laya user ({user_name})'s role: {laya_user_role}. "
                "Frame the summary and any draft replies according to these roles."
            )
    elif not laya_user_role:
        # User has no assigned role — they are monitoring, not participating
        actor_desc = f" {actor_name}'s role: {actor_role}." if actor_role else ""
        role_guidance = (
            f"\n>>> ROLE CONTEXT:{actor_desc} "
            f"The Laya user ({user_name}) has NO assigned role on this item "
            "(not a reviewer, author, assignee, or other participant). "
            "They receive this event because Laya monitors this source. "
            "Frame the card as an informational awareness update — report what "
            "happened and who is involved, but do NOT imply the user needs to "
            "review, approve, fix, or respond. Use descriptive headers, not "
            "action-oriented ones. Limit suggested actions to lightweight "
            "follow-ups (e.g., comment) rather than ownership actions "
            "(e.g., approve, request changes)."
        )

    return base + role_guidance


def build_stager_messages(
    event: LayaEvent,
    router_output: RouterOutput,
    worker_results: list[WorkerResult] | None = None,
    related_context: list[dict[str, Any]] | None = None,
    entity_history: list[dict[str, Any]] | None = None,
    user_identity: dict[str, str] | None = None,
    actor_relationship: str = "external",
    participant_roles: dict[str, Any] | None = None,
    existing_tags: list[str] | None = None,
    strictness: str = "strict",
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

    # Related context from ChromaDB (includes card_id + entity for context matching)
    context_text = ""
    if related_context:
        context_text = "\n\nRelated past cards (from memory):\n"
        for i, ctx in enumerate(related_context, 1):
            meta = ctx.get("metadata", {})
            card_id_ref = meta.get("card_id", "?")
            entity_ref = meta.get("entity_refs", "?")
            context_text += (
                f"  {i}. [card_id: {card_id_ref}] [entity: {entity_ref}] "
                f"[{meta.get('source_platform', '?')}] "
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
        pr = participant_roles or {}
        actor_role = pr.get("actor_role")
        laya_user_role = pr.get("laya_user_role")

        directive = _build_role_directive(
            actor_name, user_name, actor_role, laya_user_role, actor_relationship,
        )

        # Build participant roster (if available)
        roster_text = ""
        participants = pr.get("participants", [])
        if participants:
            roster_lines = []
            for p in participants:
                label = f"{p['name'] or p.get('handle', '?')} — {p['role']}"
                if p.get("relationship") == "self":
                    label += " (Laya user)"
                roster_lines.append(f"  • {label}")
            roster_text = "\nParticipants:\n" + "\n".join(roster_lines)

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
            f"{role_line}"
            f"{roster_text}\n"
            f">>> {directive}\n"
            f"[END ACTOR CONTEXT]"
        )

    # Platform-specific allowed actions.  The list comes from the egress
    # capability registry (which is kept in parity with the n8n executor
    # workflows) so adding a new action only requires updating one place.
    supported_actions_text = format_supported_actions(event.source.platform)
    supported_actions_block = (
        f"\n\n{supported_actions_text}" if supported_actions_text else ""
    )

    existing_tags_text = ""
    if existing_tags:
        existing_tags_text = (
            f"\n\n[EXISTING TAGS]\n{', '.join(existing_tags)}\n"
            "Prefer reusing these names when they fit. You may also suggest new tags.\n"
            "[END EXISTING TAGS]"
        )

    user_message = f"""\
Synthesize the following event and findings into a polished action card.

{event_text}

Router classification:
{classification}{entities_text}{plan_text}{workers_text}{context_text}{entity_text}{identity_text}{supported_actions_block}{existing_tags_text}

Produce a JSON action card matching the required schema."""

    directive = _CONTEXT_MATCHING_DIRECTIVES.get(
        strictness, _CONTEXT_MATCHING_DIRECTIVES["balanced"]
    )
    system_prompt = STAGER_SYSTEM_PROMPT.format(
        context_matching_directive=directive,
    )

    return [
        {"role": "system", "content": get_prompt("stager", system_prompt)},
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
                "suggested_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-3 short lowercase tag names categorizing this event",
                },
                "context_match": {
                    "type": "object",
                    "description": "Cross-entity context match from related cards",
                    "properties": {
                        "matched_card_id": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "description": "card_id of a related card with DIFFERENT entity that shares real-world context, or null",
                        },
                        "label": {
                            "type": "string",
                            "description": "Short label describing the shared context (e.g. 'April electricity bill'), empty if no match",
                        },
                    },
                    "required": ["matched_card_id", "label"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "header",
                "summary",
                "intelligence_report",
                "staged_output",
                "suggested_actions",
                "privacy_tier",
                "suggested_tags",
                "context_match",
            ],
            "additionalProperties": False,
        },
    }
