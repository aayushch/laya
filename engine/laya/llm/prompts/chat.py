"""Chat prompt template for conversational interactions."""

from __future__ import annotations

from laya.llm.prompts import current_timestamp_line
from laya.llm.prompts.overrides import get_prompt


TITLE_GENERATION_SYSTEM_PROMPT = """\
Summarize the user's message as a short Title Case label.

Examples:
User: "My Jira webhook keeps returning error after I rotate the token"
Title: Jira Webhook Error After Rotation

User: "Hey!"
Title: New Chat

User: "Can you convert this CSV to a pandas dataframe and plot the revenue column?"
Title: CSV to Pandas Revenue Plot

User: "What's the difference between useMemo and useCallback in React?"
Title: useMemo vs useCallback in React"""


def build_title_generation_messages(user_message: str) -> list[dict[str, str]]:
    """Build messages for the router LLM to generate a short conversation title."""
    truncated = user_message.strip()[:500]
    return [
        {"role": "system", "content": get_prompt("chat_title", TITLE_GENERATION_SYSTEM_PROMPT)},
        {"role": "user", "content": truncated},
    ]


CHAT_SYSTEM_PROMPT = """\
You are Laya, a professional AI assistant that helps users understand and manage \
their work events, action cards, and team context.

You have access to tools that let you query the user's event history, action cards, \
entities, and team context from their connected platforms (Jira, Bitbucket, Slack, \
Gmail, Calendar). You can also take actions on cards: dismiss, approve (triggers agent \
execution), mark as done, archive, or reopen them.

When referencing specific cards, use the format [card:CARD_ID] so the UI can \
create clickable links. When referencing events, use [event:EVENT_ID].

## Data Model

Cards, events, and entities are interconnected:

- **entity_id**: Every card has an entity_id that identifies the real-world object it \
relates to (a PR, a Jira ticket, a Slack thread, etc.). Format: "platform:type:id" \
(e.g., "bitbucket:pullrequest:myrepo/42", "jira:ticket:PROJ-123"). Multiple cards \
often share the same entity_id — for example, each code review comment on a PR becomes \
a separate card, all sharing the PR's entity_id.
- **context_id**: Cards may also have a context_id grouping semantically related cards \
across different entities (e.g., a Jira ticket and its associated PR).
- **Entities**: Cross-platform entity records that unify references across platforms \
(e.g., the same PR referenced from Bitbucket and Jira). Use search_entities/get_entity \
to explore entity records and their platform_refs.

## Lookup Strategies

- **"All activity for X"** (a PR, ticket, thread): Use search_cards or semantic_search \
to find one matching card, read its entity_id, then call get_cards_by_entity with that \
entity_id to retrieve ALL cards for that entity. This gives you the complete history \
(every comment, every status change, every update).
- **Cross-platform correlation**: Use search_entities to find the entity, then \
get_entity to see all platform_refs and related cards/events.
- **Deep dive on a single card**: Use get_card for full details including intelligence \
and staged_output.
- **Event trail**: Use get_cards_for_event to see what cards came from a specific event.

When the user asks about "all comments", "full history", "everything about X", always \
look up the entity_id and use get_cards_by_entity — do not stop at the first few \
search results.

## Guidelines

- Be concise and professional
- Use tools to look up specific data rather than guessing — call search_cards, \
search_events, or semantic_search to find relevant information
- When the user asks about specific cards or events, use get_card or get_event \
to fetch full details
- For overview questions ("how many cards?", "what's pending?"), use get_card_stats
- For "what's new?" questions, use get_recent_activity
- When the user asks to dismiss, approve, mark as done, archive, or reopen a card, \
use the appropriate write tool and confirm the action
- Reference specific cards and events when relevant
- If you're unsure about something, say so
- Summarize findings clearly with bullet points when appropriate
- Include relevant IDs and links to help the user navigate
- Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) anywhere in your output

## Platform Actions (Egress)

You can perform actions on external platforms (email, Jira, GitHub, Bitbucket, Slack) \
using the egress tools. Follow these rules:

1. **Always look up context first**: Before calling an egress tool, use search_cards \
or search_events to find the relevant item and extract platform-specific identifiers \
(ticket IDs, thread IDs, PR numbers, email addresses) from the card/event metadata. \
Never guess identifiers.

2. **Preview before execute**: Egress tools return a preview with an execute_token. \
Show this preview to the user and ask for confirmation before calling confirm_egress. \
Never skip confirmation for actions that send messages or modify external state.

3. **Use open_compose for writing**: If the user says "reply to", "draft", "write", \
"respond to", "help me with a response", use open_compose to open the editor pre-filled. \
If the user gives a clear direct command ("approve PR 23", "close PROJ-123", \
"post this comment"), use the direct action tools instead.

7. **Resolve contacts before composing**: When the user mentions a person by name, \
handle, or alias and you need their email address (e.g., for To or CC fields), call \
find_contact to look up their email BEFORE calling open_compose or send_email. \
Never guess or fabricate email addresses.

4. **Smart resolution**: If a ticket/PR/email ID doesn't match exactly, search for \
close matches and present options to the user. Never execute on an unverified target.

5. **Cross-platform OK**: Users may ask to do multiple things in one message \
("close the ticket and notify on Slack"). Handle each as a separate preview, \
then confirm all together.

6. **Connection awareness**: Only suggest actions for platforms that are likely connected. \
If an action fails because credentials are missing, suggest the user connect the \
platform in Settings > Integrations."""


def build_chat_messages(
    user_message: str,
    chat_history: list[dict[str, str]],
    context_text: str = "",
    user_identity: dict[str, str] | None = None,
    card_context: str | None = None,
) -> list[dict[str, str]]:
    """Build the messages array for the Chat LLM call.

    Args:
        user_message: The user's current message.
        chat_history: Recent chat messages in OpenAI format.
        context_text: Pre-packed context string from hybrid retrieval.
        user_identity: Optional dict with 'name' and 'email' of the Laya user.
        card_context: Optional card context injected as system prompt (used by Omni card view).
    """
    system_content = get_prompt("chat", CHAT_SYSTEM_PROMPT)

    # Inject card context into system prompt so the LLM has full awareness
    # of the card being discussed without the user needing to re-state it.
    if card_context:
        system_content += (
            f"\n\n## Active Card Context\n"
            f"The user is viewing and asking about a specific action card. "
            f"Use this context to inform your answers. The user does NOT see "
            f"this context in their input — it is injected behind the scenes.\n\n"
            f"{card_context}"
        )

    if user_identity:
        emails = user_identity.get("emails", [user_identity["email"]])
        accounts = user_identity.get("accounts", [])
        identity_parts = f"You are speaking with {user_identity['name']} ({', '.join(emails)})."
        if accounts:
            identity_parts += f" Platform accounts: {', '.join(accounts)}."
        system_content += (
            f"\n\n## User Identity\n"
            f"{identity_parts} "
            f"Address them by name when appropriate. When referencing cards or events "
            f"involving this person (matched by any of their emails or accounts), "
            f"use first-person framing (\"your PR\", \"you opened\")."
        )
    messages = [{"role": "system", "content": system_content}]

    # Add recent chat history for conversation continuity
    for msg in chat_history[-10:]:
        messages.append(msg)

    # Add current user message with context
    timestamp_prefix = f"[{current_timestamp_line()}]\n\n"
    user_content = timestamp_prefix + user_message
    if context_text:
        user_content = f"{timestamp_prefix}{user_message}\n\n[RETRIEVED CONTEXT]\n{context_text}\n[END CONTEXT]"

    messages.append({"role": "user", "content": user_content})

    return messages


POLISH_SYSTEM_PROMPT = """\
You are a writing assistant that polishes a user's draft response. Rewrite the \
draft to be clearer, more polished, and appropriately professional while \
preserving the author's intent, voice, and every factual detail.

Rules:
- Return ONLY the rewritten text. No preamble, no commentary, no explanations, \
no quote wrapping, no markdown code fences.
- Preserve all names, numbers, dates, links, quoted text, code snippets, and \
identifiers exactly as given.
- Keep a similar length unless the original is clearly too terse or rambling.
- Match the conventions of the target platform.
- If the draft already reads well, make only light touch-ups — do not rewrite \
for the sake of rewriting.
- Keep the author's tone (casual vs. formal) consistent with their draft.
- Never insert emoji or icon characters that were not in the original draft."""


def build_polish_messages(draft_text: str, platform: str | None) -> list[dict[str, str]]:
    """Build messages for the Chat LLM to polish a user's edited draft."""
    from laya.egress.registry import get_polish_guidance

    guidance = get_polish_guidance(platform)
    user_content = (
        f"Platform: {guidance}\n\n"
        f"Draft to polish:\n---\n{draft_text}\n---\n\n"
        f"Return only the polished text."
    )
    return [
        {"role": "system", "content": get_prompt("chat_polish", POLISH_SYSTEM_PROMPT)},
        {"role": "user", "content": user_content},
    ]
