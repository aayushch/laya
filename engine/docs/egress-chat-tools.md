# Egress Chat Tools

> Detailed design for chat-driven egress — LLM tool definitions, interaction patterns,
> and the preview/confirm flow.
> Part of the [Egress Architecture](egress-architecture.md).

---

## 1. Overview

Every egress capability is exposed as an LLM tool in the chat pipeline. This makes
chat a **first-class control surface** — users can trigger any platform action by
describing it in natural language.

The tools are added to `get_all_tool_definitions()` and executed by the existing chat
tool loop in `pipeline/chat.py`.

### Design Goals

- **Natural language**: "Approve PR 23" should just work.
- **Smart resolution**: Fuzzy match ticket/PR identifiers, suggest corrections for typos.
- **Preview before execute**: User always sees what will happen before confirming.
- **Compose bridge**: "Reply to Sarah's email" opens the compose editor, not sends directly.
- **Cross-platform**: "Close the Jira ticket and notify on Slack" works as a single command.

---

## 2. Tool Definitions

### 2.1 send_email

```python
{
    "name": "send_email",
    "description": (
        "Send an email or reply to an email thread. Use when the user wants to "
        "email someone, reply to an email, or forward an email. "
        "IMPORTANT: Before calling this, use search_cards or search_events to find "
        "the relevant email thread and extract the recipient, subject, and thread_id "
        "from the card/event metadata. Do not guess email addresses."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address"
            },
            "subject": {
                "type": "string",
                "description": "Email subject line (auto-prefixed with 'Re: ' for replies)"
            },
            "body": {
                "type": "string",
                "description": "Email body text (supports HTML)"
            },
            "thread_id": {
                "type": "string",
                "description": "Gmail/Outlook thread ID for replies (from event metadata)"
            },
            "cc": {
                "type": "string",
                "description": "CC recipients, comma-separated (optional)"
            },
            "bcc": {
                "type": "string",
                "description": "BCC recipients, comma-separated (optional)"
            },
            "platform": {
                "type": "string",
                "enum": ["gmail", "outlook", "smtp"],
                "description": "Which email platform to send from. Infer from context."
            },
        },
        "required": ["to", "subject", "body"],
    },
}
```

### 2.2 comment_on_ticket

```python
{
    "name": "comment_on_ticket",
    "description": (
        "Post a comment on a Jira ticket, GitHub issue, or Linear issue. "
        "Use search_cards or search_events first to find the ticket and extract "
        "its platform-specific identifier from metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["jira", "github", "linear"],
                "description": "Platform hosting the ticket"
            },
            "ticket_id": {
                "type": "string",
                "description": (
                    "Ticket identifier. Jira: 'PROJ-123'. "
                    "GitHub: 'owner/repo#45'. Linear: issue ID."
                )
            },
            "comment": {
                "type": "string",
                "description": "Comment body text (supports markdown)"
            },
        },
        "required": ["platform", "ticket_id", "comment"],
    },
}
```

### 2.3 transition_ticket

```python
{
    "name": "transition_ticket",
    "description": (
        "Change the status of a Jira or Linear ticket (e.g., 'In Progress' -> 'Done'). "
        "Use search_cards to find the ticket first. If the user says 'close' or 'resolve', "
        "map to the appropriate terminal status for that platform."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["jira", "linear"],
            },
            "ticket_id": {
                "type": "string",
                "description": "Ticket identifier (e.g., 'PROJ-123')"
            },
            "target_status": {
                "type": "string",
                "description": (
                    "New status name. Common values: 'To Do', 'In Progress', "
                    "'In Review', 'Done', 'Closed', 'Resolved'. "
                    "The executor will resolve this to the correct transition ID."
                )
            },
            "comment": {
                "type": "string",
                "description": "Optional comment to add with the transition"
            },
        },
        "required": ["platform", "ticket_id", "target_status"],
    },
}
```

### 2.4 create_ticket

```python
{
    "name": "create_ticket",
    "description": (
        "Create a new ticket/issue on Jira, GitHub, or Linear. "
        "Use when the user says 'create a ticket', 'file an issue', 'open a bug', etc."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["jira", "github", "linear"],
            },
            "project": {
                "type": "string",
                "description": (
                    "Project/repo. Jira: project key ('PROJ'). "
                    "GitHub: 'owner/repo'. Linear: team key."
                )
            },
            "title": {
                "type": "string",
                "description": "Issue title/summary"
            },
            "description": {
                "type": "string",
                "description": "Issue description (supports markdown)"
            },
            "type": {
                "type": "string",
                "description": "Issue type: 'bug', 'task', 'story', 'epic' (Jira/Linear only)"
            },
            "priority": {
                "type": "string",
                "description": "Priority: 'lowest', 'low', 'medium', 'high', 'highest'"
            },
            "assignee": {
                "type": "string",
                "description": "Assignee email or username (optional)"
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated labels (optional)"
            },
        },
        "required": ["platform", "project", "title"],
    },
}
```

### 2.5 pr_action

```python
{
    "name": "pr_action",
    "description": (
        "Perform an action on a pull request: approve, request changes, comment, "
        "merge, or decline. Use search_cards to find the PR first and extract the "
        "PR identifier from card/event metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["github", "bitbucket"],
            },
            "pr_id": {
                "type": "string",
                "description": (
                    "PR identifier. GitHub: 'owner/repo#123'. "
                    "Bitbucket: 'workspace/repo/45' or just '45' if context is clear."
                )
            },
            "action": {
                "type": "string",
                "enum": ["approve", "request_changes", "comment", "merge", "decline"],
                "description": "What to do with the PR"
            },
            "comment": {
                "type": "string",
                "description": "Comment body (required for 'comment' and 'request_changes')"
            },
            "merge_strategy": {
                "type": "string",
                "enum": ["merge", "squash", "rebase"],
                "description": "Merge strategy (only for 'merge' action, default: 'squash')"
            },
        },
        "required": ["platform", "pr_id", "action"],
    },
}
```

### 2.6 send_slack_message

```python
{
    "name": "send_slack_message",
    "description": (
        "Send a Slack message to a channel or reply to a thread. "
        "Use search_cards/search_events to find the channel and thread_ts for replies. "
        "For new messages, ask the user which channel to post in."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Channel name (e.g., '#general') or Slack channel ID"
            },
            "message": {
                "type": "string",
                "description": "Message text (supports Slack mrkdwn formatting)"
            },
            "thread_ts": {
                "type": "string",
                "description": "Thread timestamp for thread replies (from slack_thread_ts in event metadata)"
            },
        },
        "required": ["channel", "message"],
    },
}
```

### 2.7 open_compose

```python
{
    "name": "open_compose",
    "description": (
        "Open the compose/reply editor in the UI, pre-filled with given data. "
        "Use this when the user wants to WRITE or EDIT a message before sending -- "
        "cases like 'I want to reply to...', 'draft an email to...', 'help me write a response'. "
        "For direct commands where intent is clear ('approve PR 23', 'close PROJ-123'), "
        "use the direct action tools instead.\n\n"
        "This tool sends a WebSocket event to the UI which opens the compose editor. "
        "It does NOT send/execute anything -- the user will review and send from the UI."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["gmail", "outlook", "smtp", "slack", "jira", "github", "bitbucket"],
            },
            "action_type": {
                "type": "string",
                "enum": ["reply", "compose", "comment", "forward"],
                "description": "What kind of compose to open"
            },
            "prefill": {
                "type": "object",
                "description": (
                    "Pre-filled fields for the editor. Varies by platform:\n"
                    "Email: {to, subject, body, thread_id, cc}\n"
                    "Slack: {channel, message, thread_ts}\n"
                    "Jira/GitHub: {ticket_id, comment}\n"
                    "Bitbucket: {pr_id, comment}"
                ),
            },
            "source_card_id": {
                "type": "string",
                "description": "Card ID that provides context for this compose (optional)"
            },
        },
        "required": ["platform", "action_type", "prefill"],
    },
}
```

### 2.8 confirm_egress

```python
{
    "name": "confirm_egress",
    "description": (
        "Execute a previously previewed egress action after the user has confirmed. "
        "Only call this AFTER you have shown the user a preview and they have said "
        "'yes', 'go ahead', 'confirm', 'do it', or similar affirmative response. "
        "The execute_token comes from the preview response of a prior egress tool call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "execute_token": {
                "type": "string",
                "description": "Signed execution token from the preview response"
            },
        },
        "required": ["execute_token"],
    },
}
```

---

## 3. Interaction Patterns

### 3.1 Pattern: Direct Command (Preview -> Confirm -> Execute)

User gives a clear, actionable command. The LLM looks up context, shows a preview,
and executes after confirmation.

```
User: "Approve PR 23 on Bitbucket"

LLM internal steps:
  1. search_cards(query="PR 23 bitbucket", limit=5)
     -> Finds card with PR #23 "Fix payment timeout"
     -> Extracts: workspace="acme", repo="payments", pr_number=23

  2. pr_action(platform="bitbucket", pr_id="acme/payments/23", action="approve")
     -> Egress module returns EgressPreview:
        {
          "status": "preview",
          "summary": "Approve PR #23 'Fix payment timeout' on acme/payments",
          "details": {"pr_title": "Fix payment timeout", "author": "sarah"},
          "warnings": [],
          "execute_token": "tok_abc123..."
        }

LLM response to user:
  "I'll approve **PR #23 'Fix payment timeout'** on acme/payments.
   Author: sarah. Shall I go ahead?"

User: "Yes"

LLM internal steps:
  3. confirm_egress(execute_token="tok_abc123...")
     -> Egress module executes the action
     -> Returns: {success: true, result_url: "https://bitbucket.org/..."}

LLM response:
  "Done! PR #23 approved. [View on Bitbucket](https://bitbucket.org/...)"
```

### 3.2 Pattern: Smart Resolution (Typo / Fuzzy Match)

User references a ticket/PR that doesn't exist. The LLM searches for close matches
and asks for clarification.

```
User: "Close ticket PAY-89"

LLM internal steps:
  1. search_cards(query="PAY-89")
     -> No results

  2. search_cards(query="PAY jira")
     -> Finds: PAY-98 "Fix payment timeout in checkout"
              PAY-87 "Add retry logic to payment gateway"
              PAY-90 "Payment confirmation email not sent"

  3. LLM notices PAY-89 doesn't exist but PAY-98 is a close match

LLM response:
  "I couldn't find PAY-89. Did you mean one of these?
   - **PAY-98** — Fix payment timeout in checkout
   - **PAY-87** — Add retry logic to payment gateway
   - **PAY-90** — Payment confirmation email not sent"

User: "98"

LLM internal steps:
  4. transition_ticket(platform="jira", ticket_id="PAY-98", target_status="Closed")
     -> Returns preview

LLM: "I'll close **PAY-98 'Fix payment timeout in checkout'**. Confirm?"

User: "Yes"

LLM:
  5. confirm_egress(execute_token="...")
     -> Executed

LLM: "Done! PAY-98 is now Closed."
```

### 3.3 Pattern: Open Compose (User Wants to Write)

User wants to draft/edit a message rather than send something automatically.
The LLM opens the compose editor pre-filled with context.

```
User: "I want to reply to Sarah's email about the nav redesign"

LLM internal steps:
  1. search_cards(query="Sarah nav redesign email")
     -> Finds card_xyz with gmail event

  2. get_card(card_id="card_xyz")
     -> Full card data including:
        - event metadata: {gmail_from: "sarah@co.com", gmail_threadId: "thread_abc"}
        - staged_output: {type: "draft_reply", content: "Hi Sarah, great question..."}
        - subject: "Nav redesign feedback"

  3. open_compose(
       platform="gmail",
       action_type="reply",
       prefill={
         "to": "sarah@co.com",
         "subject": "Re: Nav redesign feedback",
         "thread_id": "thread_abc",
         "body": "Hi Sarah,\n\nGreat question! The nav redesign uses..."
       },
       source_card_id="card_xyz"
     )
     -> WebSocket event sent to UI

LLM response:
  "I've opened a reply to Sarah's email about the nav redesign.
   The editor is pre-filled with a draft based on the card's context.
   Edit it as needed and hit Send when ready."

>> UI: Compose editor slides open with pre-filled data
>> User edits the draft and clicks Send
>> UI calls egress.execute() directly
```

### 3.4 Pattern: Cross-Platform Actions

User wants to perform actions across multiple platforms in one command.

```
User: "The payment bug is fixed. Close PROJ-123 and let Sarah know on Slack"

LLM internal steps:
  1. search_cards(query="PROJ-123")
     -> Finds Jira card for PROJ-123

  2. transition_ticket(platform="jira", ticket_id="PROJ-123", target_status="Done",
                       comment="Fixed — closing ticket.")
     -> Preview returned

  3. search_events(query="Sarah", platform="slack")
     -> Finds Sarah's Slack context, channel #payments

  4. send_slack_message(channel="#payments",
                        message="Hey @sarah — PROJ-123 is now fixed and closed!")
     -> Preview returned

LLM response:
  "I'll do two things:
   1. **Close PROJ-123** on Jira with comment 'Fixed — closing ticket.'
   2. **Send a message** in #payments on Slack: 'Hey @sarah — PROJ-123 is now fixed and closed!'

   Go ahead with both?"

User: "Yes"

LLM:
  5. confirm_egress(execute_token="tok_jira...")   -> Done
  6. confirm_egress(execute_token="tok_slack...")   -> Done

LLM: "Done! PROJ-123 closed on Jira and Sarah notified in #payments."
```

### 3.5 Pattern: Compose New (No Existing Card)

User wants to create something from scratch, not related to an existing notification.

```
User: "Send an email to john@partner.com asking about the Q2 deliverables"

LLM internal steps:
  1. LLM drafts the email content based on context

  2. open_compose(
       platform="gmail",
       action_type="compose",
       prefill={
         "to": "john@partner.com",
         "subject": "Q2 Deliverables Status",
         "body": "Hi John,\n\nHope you're doing well. I wanted to touch base on..."
       }
     )

LLM: "I've opened a new email to john@partner.com with a draft about Q2 deliverables.
      Review and edit the draft, then hit Send."
```

---

## 4. Preview/Confirm Flow (Technical Details)

### 4.1 How Preview Works

When an egress tool is called, the handler calls `egress.preview()` instead of
`egress.execute()`:

```python
# engine/laya/egress/tool_handlers.py

async def handle_egress_tool(tool_name: str, arguments: dict, space_id: str) -> str:
    """Universal handler for all egress tools (except open_compose and confirm_egress)."""

    # Build EgressRequest from tool arguments
    request = _build_request_from_tool(tool_name, arguments, space_id)

    # Get preview (does NOT execute)
    preview = await egress.preview(request)

    # Generate a signed, time-limited execute token
    # Token encodes: request hash, space_id, timestamp, nonce
    execute_token = _sign_execute_token(request)

    # Store the pending request keyed by token (for confirm_egress to retrieve)
    _pending_requests[execute_token] = request

    # Return preview to the LLM
    return json.dumps({
        "status": "preview",
        "summary": preview.summary,
        "details": preview.details,
        "warnings": preview.warnings,
        "execute_token": execute_token,
    })
```

### 4.2 How Confirm Works

When the user confirms, the LLM calls `confirm_egress`:

```python
async def handle_confirm_egress(arguments: dict, space_id: str) -> str:
    """Execute a previously previewed action."""
    token = arguments["execute_token"]

    # Retrieve the pending request
    request = _pending_requests.pop(token, None)
    if not request:
        return json.dumps({"status": "error", "error": "Token expired or already used"})

    # Validate token signature and expiry (tokens expire after 5 minutes)
    if not _validate_token(token):
        return json.dumps({"status": "error", "error": "Token expired"})

    # Execute for real
    result = await egress.execute(request)

    return json.dumps({
        "status": "done" if result.success else "failed",
        "result_url": result.result_url,
        "error": result.error,
    })
```

### 4.3 How open_compose Works

The `open_compose` tool doesn't go through egress at all — it sends a WebSocket event:

```python
async def handle_open_compose(arguments: dict, space_id: str) -> str:
    """Send a WebSocket event to open the compose editor in the UI."""
    from laya.api.websocket import manager

    await manager.broadcast({
        "type": "open_compose",
        "platform": arguments["platform"],
        "action_type": arguments["action_type"],
        "prefill": arguments["prefill"],
        "source_card_id": arguments.get("source_card_id"),
    })

    platform = arguments["platform"]
    action = arguments["action_type"]
    return json.dumps({
        "status": "compose_opened",
        "message": f"Opened {action} editor for {platform}. User can edit and send from the UI."
    })
```

---

## 5. Wiring Into the Chat Pipeline

### 5.1 Tool Registration

The egress tools are added to the chat's tool list:

```python
# engine/laya/llm/tools/definitions.py

from laya.egress.tools import get_egress_tool_definitions

def get_all_tool_definitions() -> list[dict]:
    """Return all tool definitions in OpenAI function calling format."""
    return [
        *_read_tools(),
        *_write_tools(),
        *_settings_tools(),
        *get_egress_tool_definitions(),  # <-- NEW
    ]
```

### 5.2 Tool Execution

The egress tool handlers are registered in the executor:

```python
# engine/laya/llm/tools/executor.py

from laya.egress.tool_handlers import (
    handle_egress_tool,
    handle_open_compose,
    handle_confirm_egress,
    EGRESS_TOOL_NAMES,
)

async def execute_tool(name: str, arguments: dict, space_id: str | None = None) -> str:
    # ... existing tool handlers ...

    # Egress tools
    if name == "open_compose":
        return await handle_open_compose(arguments, space_id)
    if name == "confirm_egress":
        return await handle_confirm_egress(arguments, space_id)
    if name in EGRESS_TOOL_NAMES:
        return await handle_egress_tool(name, arguments, space_id)

    return json.dumps({"error": f"Unknown tool: {name}"})
```

### 5.3 MCP Server Extension

The same egress tools are also exposed via the MCP server, so Claude Desktop and other
MCP clients can trigger platform actions through Laya:

```python
# engine/laya/mcp/server.py

from laya.egress.tools import get_egress_tool_definitions

@server.list_tools()
async def list_tools():
    all_tools = get_all_tool_definitions()  # Includes egress tools
    return [convert_to_mcp_format(t) for t in all_tools]
```

---

## 6. LLM Guidance (System Prompt Additions)

The chat system prompt needs additions to guide the LLM on when and how to use
egress tools:

```
## Platform Actions

You can perform actions on external platforms (email, Jira, GitHub, Bitbucket, Slack)
using the egress tools. Follow these rules:

1. **Always look up context first**: Before calling an egress tool, use search_cards
   or search_events to find the relevant item and extract platform-specific identifiers
   (ticket IDs, thread IDs, PR numbers, email addresses) from the card/event metadata.

2. **Preview before execute**: Egress tools return a preview. Show this to the user
   and ask for confirmation before calling confirm_egress. Never skip confirmation.

3. **Use open_compose for writing**: If the user says "reply to", "draft", "write",
   "respond to", use open_compose to open the editor. If the user gives a clear
   direct command ("approve PR 23", "close PROJ-123"), use the direct action tools.

4. **Smart resolution**: If a ticket/PR ID doesn't match, search for close matches
   and ask the user to clarify. Never guess.

5. **Cross-platform OK**: Users may ask you to do multiple things in one message
   ("close the ticket and notify on Slack"). Handle each as a separate egress action,
   show all previews, then confirm all at once.

6. **Respect limitations**: Only suggest actions for platforms the user has connected.
   If a platform isn't connected, suggest they connect it in Settings > Integrations.
```
