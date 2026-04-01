# Laya Egress Architecture

> Comprehensive design document for Laya's outbound action (egress) system.
> This document covers architecture, module structure, credential management,
> chat integration, card UI integration, platform coverage, and implementation roadmap.

**Status**: Design complete, implementation pending
**Date**: 2026-03-30
**Related docs**: [egress-connection-broker.md](egress-connection-broker.md) | [egress-chat-tools.md](egress-chat-tools.md) | [egress-platform-matrix.md](egress-platform-matrix.md) | [egress-implementation-checklist.md](egress-implementation-checklist.md)

---

## 1. Problem Statement

Laya currently excels at **ingestion** — consolidating notifications from 8+ platforms
into a unified, AI-processed feed. However, to respond to those notifications, users
must switch back to each individual platform (Gmail to reply, Jira to comment, Bitbucket
to approve a PR). This makes Laya a read-only notification viewer rather than a true
communication platform.

For Laya to succeed, it must support robust **egress** — the ability to take action on
any platform directly from within Laya, whether through the card UI or through natural
language chat commands.

### Current State (What Exists)

| Platform | Executor Workflow | Actions Supported |
|----------|-------------------|-------------------|
| Gmail | `gmail-executor.json` | Send/reply (no attachments, no forward, no archive) |
| GitHub | `github-executor.json` | Close issue, comment on issue (no PR actions) |
| Google Calendar | `google-calendar-executor.json` | Create event |
| Outlook Email | `outlook-email-executor.json` | Send/reply |
| Outlook Calendar | `outlook-calendar-executor.json` | Create event |
| Jira | **None** | Nothing |
| Bitbucket | **None** | Nothing |
| Slack | **None** | Nothing |
| Linear | **None** | Nothing |

### What's Missing

- **3 critical executor workflows** (Jira, Slack, Bitbucket) don't exist at all
- **No user-initiated compose** — users can only act on LLM-suggested actions
- **No inline reply/edit UI** — no rich text area for modifying drafts before sending
- **No file attachment support** for any platform
- **No chat-driven egress** — chat tools only support card management, not platform actions
- **Incomplete platform coverage** — even existing executors are partial (Gmail has no forward/archive)

---

## 2. Design Principles

These principles are non-negotiable and govern every design decision:

### P1: Laya = Orchestrator, Never Executor

Laya Engine must **never** directly call platform APIs (Gmail API, Jira REST, Slack SDK,
etc.). Laya sends instructions to execution backends; the backends perform the physical
work. This keeps Laya lightweight, portable, and focused on intelligence.

**Analogy**: If LLMs are the brain, Laya is the spinal cord, and n8n is the hands and feet.

### P2: n8n = Primary Execution Layer

n8n remains the primary backend for all platform actions. Missing capabilities are solved
by building new n8n executor workflows, not by adding SDK calls to the Engine. n8n already
has native nodes for every platform Laya supports.

### P3: Single Setup, Zero Fragmentation

Users configure credentials **once** in Laya Settings. The system provisions credentials
to wherever they need to go (n8n, OS keychain, etc.) transparently. Users should never
need to open the n8n dashboard, configure OAuth in a separate system, or manage credentials
in multiple places.

### P4: Egress Is a Separate Module

The egress system is a self-contained Python package (`engine/laya/egress/`) with its own
public API. The Engine is a **consumer** of the egress module — it calls `egress.execute()`,
never builds n8n payloads or knows about webhook URLs directly.

### P5: Chat = First-Class Control Surface

Every egress capability must be available as an LLM tool. Chat is not secondary to the
card UI — it's a first-class way to trigger actions. A user saying "approve PR 23" in chat
should work just as well as clicking the Approve button on the card.

### P6: Card UI = Direct Interaction

Cards also get inline editors and quick-action bars. Both surfaces (chat and cards) consume
the same egress module through the same API.

---

## 3. Module Architecture

### 3.1 Package Structure

```
engine/laya/egress/                  <-- NEW top-level package
|
+-- __init__.py                      Public API: execute(), preview(), get_capabilities(),
|                                    list_connections(), connect(), disconnect()
|
+-- models.py                        EgressRequest, EgressResult, EgressPreview,
|                                    EgressCapability, Connection, ConnectionResult
|
+-- router.py                        Routes EgressRequest to correct backend
|                                    Priority: MCP (future) > n8n > SMTP fallback
|
+-- connections.py                   Connection Broker: single-pane credential management
|                                    Handles API keys, OAuth flows, SMTP configs
|                                    Provisions credentials to backends (n8n, keychain)
|
+-- registry.py                      Platform capability matrix
|                                    Maps platform -> list of supported action_types
|                                    Used by stager (what actions to suggest) and UI (what buttons to show)
|
+-- tools.py                         LLM tool definitions for chat-driven egress
|                                    Returned by get_egress_tool_definitions()
|
+-- tool_handlers.py                 Execution handlers for egress tools
|                                    Called by chat tool loop when LLM invokes an egress tool
|
+-- backends/                        Pluggable executor backends
|   +-- __init__.py
|   +-- base.py                      Abstract EgressBackend interface
|   +-- n8n.py                       n8n webhook executor (primary backend)
|   +-- smtp.py                      SMTP/IMAP executor (generic email fallback)
|
+-- platforms/                       Platform-specific payload builders and validators
    +-- __init__.py
    +-- gmail.py                     Gmail payload normalization, validation, field mapping
    +-- jira.py                      Jira payload normalization, transition resolution
    +-- github.py                    GitHub payload normalization, owner/repo parsing
    +-- bitbucket.py                 Bitbucket payload normalization
    +-- slack.py                     Slack payload normalization, channel resolution
    +-- outlook.py                   Outlook payload normalization
    +-- linear.py                    Linear GraphQL payload building
    +-- calendar.py                  Google/Outlook Calendar payload normalization
```

### 3.2 Public API

The Engine sees **only** these functions. Everything else is internal to the egress module.

```python
# engine/laya/egress/__init__.py

async def execute(request: EgressRequest) -> EgressResult:
    """Execute an outbound action.

    This is the ONLY entry point for performing platform actions.
    The Engine calls this. Egress determines HOW to execute it
    (n8n, SMTP, MCP, etc.) — the Engine never knows or cares.

    Args:
        request: Describes what to do (platform, action_type, payload, etc.)

    Returns:
        EgressResult with success/failure, result URL, and error details.
    """

async def preview(request: EgressRequest) -> EgressPreview:
    """Return a preview of what would happen without executing.

    Used by:
    - Chat confirmation flow (LLM shows preview, user confirms)
    - UI action confirmation modal
    - Dry-run validation

    Args:
        request: The action to preview.

    Returns:
        EgressPreview with human-readable summary, structured details, and warnings.
    """

async def get_capabilities(platform: str) -> list[EgressCapability]:
    """What actions can this platform perform?

    Used by:
    - Stager: to know what suggested_actions to generate for cards
    - UI: to show available quick-action buttons on cards
    - Chat: to inform the LLM about available actions

    Args:
        platform: Platform identifier (e.g., "gmail", "jira", "slack")

    Returns:
        List of EgressCapability describing each available action.
    """

async def list_connections() -> list[Connection]:
    """List all configured platform connections and their health status.

    Used by Settings UI to show connected/disconnected platforms.
    """

async def connect(platform: str, credentials: dict) -> ConnectionResult:
    """Single entry point for credential setup.

    Validates credentials, stores in keychain, and provisions to
    all backends that need them (n8n, etc.).

    Args:
        platform: Platform identifier
        credentials: Platform-specific credential dict

    Returns:
        ConnectionResult with status and available capabilities.
    """

async def disconnect(platform: str, connection_id: str) -> None:
    """Remove a platform connection.

    Cleans up credentials from all backends (keychain, n8n, etc.).
    """
```

### 3.3 Data Models

```python
# engine/laya/egress/models.py

class EgressRequest:
    """A request to perform an outbound action."""
    platform: str              # "gmail", "jira", "slack", "github", "bitbucket", etc.
    action_type: str           # "send_email", "comment", "approve_pr", "transition", etc.
    payload: dict              # Platform-specific action data
    source_card_id: str | None # Card that triggered this (for context + logging)
    source_event_id: str | None # Original event (for metadata like thread_id)
    space_id: str | None       # For credential/executor resolution
    dry_run: bool = False      # Validate without executing

class EgressResult:
    """Result of an egress action."""
    success: bool
    result_url: str | None     # Link to the created/modified resource
    result_data: dict          # Platform-specific response data
    error: str | None          # Error message if failed
    retryable: bool = False    # Whether the action can be retried

class EgressPreview:
    """Preview of what an egress action will do (shown before confirmation)."""
    platform: str
    action_type: str
    summary: str               # Human-readable: "Send email to sarah@co.com"
    details: dict              # Structured for UI rendering
    warnings: list[str]        # "This will send to 5 recipients"
    estimated_impact: str      # "low" | "medium" | "high"

class EgressCapability:
    """One action a platform can perform."""
    action_type: str           # "comment", "send_email", "approve_pr"
    label: str                 # "Post Comment" (human-readable button text)
    requires_fields: list[str] # Fields that must be present in payload
    optional_fields: list[str] # Fields that may be present
    description: str           # What this action does
    confirmation_required: bool # Whether to show confirmation before executing

class Connection:
    """A configured platform connection."""
    connection_id: str
    platform: str
    name: str                  # User-given name or auto-generated
    status: str                # "connected" | "error" | "expired"
    capabilities: list[str]    # Action types available
    created_at: str
    last_used_at: str | None
    error: str | None          # If status is "error"

class ConnectionResult:
    """Result of connecting a platform."""
    status: str                # "connected" | "failed"
    connection_id: str | None
    capabilities: list[str]    # Actions now available
    error: str | None
```

### 3.4 How Engine Consumes Egress

The current `pipeline/executor.py` (354 lines of n8n-specific logic) gets refactored
into a thin adapter that delegates to the egress module:

```python
# engine/laya/pipeline/executor.py (REFACTORED)

from laya.egress import execute as egress_execute
from laya.egress.models import EgressRequest

async def execute_action(card_id: str, action_id: str, modifications: dict | None = None) -> dict:
    """Execute a suggested action on a card. Delegates entirely to egress module."""

    # 1. Look up card + action from DB (unchanged from today)
    card_row, action = await _lookup_card_action(card_id, action_id)

    # 2. Apply user modifications to payload (unchanged)
    payload = _merge_payload(action, modifications)

    # 3. Update card to 'executing' + broadcast (unchanged)
    await _set_card_executing(card_id, action_id)

    # 4. Build EgressRequest and delegate
    request = EgressRequest(
        platform=action["target_platform"],
        action_type=action["action_type"],
        payload=payload,
        source_card_id=card_id,
        source_event_id=card_row["event_id"],
        space_id=await _get_card_space_id(card_id),
    )
    result = await egress_execute(request)

    # 5. Record result in action_log + update card status (unchanged)
    await _record_result(card_id, action_id, action, result)

    # 6. Broadcast final status via WebSocket (unchanged)
    await _broadcast_result(card_id, result)

    return {
        "card_id": card_id,
        "action_id": action_id,
        "status": "done" if result.success else "failed",
        "result_url": result.result_url,
        "error": result.error,
    }
```

The Engine retains responsibility for: card lifecycle, status management, action logging,
WebSocket broadcasting. It no longer builds n8n payloads, resolves webhook URLs, or knows
about any execution backend.

---

## 4. Backend Execution Strategy

### 4.1 Backend Interface

All execution backends implement this interface:

```python
# engine/laya/egress/backends/base.py

class EgressBackend(abc.ABC):
    """Abstract interface for egress execution backends."""

    @abc.abstractmethod
    async def execute(self, request: EgressRequest, credentials: dict) -> EgressResult:
        """Execute an outbound action using this backend."""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the backend is reachable and operational."""

    @abc.abstractmethod
    def supports(self, platform: str, action_type: str) -> bool:
        """Check if this backend can handle the given platform + action."""
```

### 4.2 n8n Backend (Primary)

The n8n backend POSTs to executor webhook URLs, exactly as today's `executor.py` does.
The key change is that this logic lives inside the egress module rather than in the
Engine's pipeline:

```python
# engine/laya/egress/backends/n8n.py

class N8nBackend(EgressBackend):
    """Primary execution backend. Routes actions to n8n executor workflows."""

    async def execute(self, request: EgressRequest, credentials: dict) -> EgressResult:
        # 1. Resolve webhook URL (space-aware, with global fallback)
        webhook_url = await self._resolve_webhook(request.platform, request.space_id)

        # 2. Build n8n payload (delegates to platform-specific builder)
        payload = build_n8n_payload(request)

        # 3. POST to n8n with retry logic
        response = await self._post_with_retry(webhook_url, payload)

        # 4. Parse response into EgressResult
        return self._parse_response(response)
```

### 4.3 SMTP Backend (Generic Email Fallback)

For email providers not covered by Gmail/Outlook (ProtonMail, Fastmail, Yahoo, iCloud,
Zoho, etc.), a lightweight SMTP backend sends email directly:

```python
# engine/laya/egress/backends/smtp.py

class SmtpBackend(EgressBackend):
    """Send email via SMTP. Fallback for providers without dedicated n8n executors.

    This is the ONE execution path that runs inside the Laya process rather than
    delegating to n8n. It exists because n8n's generic SMTP/IMAP nodes don't
    handle threading (In-Reply-To headers), diverse provider quirks, or
    attachment encoding reliably enough.

    From the Engine's perspective, this is invisible — it calls egress.execute()
    and the router decides to use SMTP instead of n8n. The Engine never knows.
    """

    async def execute(self, request: EgressRequest, credentials: dict) -> EgressResult:
        # Build MIME message with proper threading headers
        # Handle attachments (base64 decode, MIME attach)
        # Send via aiosmtplib
```

**Why this doesn't violate Principle P1**: The SMTP backend lives inside the egress
module, which is a *separate concern* from the Engine. The Engine remains a pure
orchestrator — it calls `egress.execute()` and doesn't know whether the email went
through n8n or SMTP. The egress module is permitted to have its own execution strategies.

### 4.4 MCP Backend (Future, Optional)

As the MCP ecosystem matures, platforms will ship their own MCP servers (Slack, GitHub,
and others already have community MCP servers). The egress router can support MCP as an
opt-in backend:

```python
# engine/laya/egress/backends/mcp.py (FUTURE)

class McpBackend(EgressBackend):
    """Execute actions via external MCP servers.

    If a user has a Slack MCP server configured, egress can use it instead
    of n8n for Slack actions. This is opt-in and configured per-platform.
    """

    async def execute(self, request: EgressRequest, credentials: dict) -> EgressResult:
        server = self.get_mcp_server(request.platform)
        result = await server.call_tool(
            name=request.action_type,
            arguments=request.payload,
        )
        return EgressResult(success=True, result_data=result)
```

### 4.5 Backend Priority Resolution

The router selects the best backend for each request:

```python
# engine/laya/egress/router.py

class EgressRouter:
    """Routes egress requests to the best available backend."""

    async def resolve_backend(self, request: EgressRequest) -> EgressBackend:
        platform = request.platform

        # Priority 1: MCP server configured for this platform? (future, opt-in)
        if mcp_server := self.mcp_registry.get(platform):
            if mcp_server.supports(platform, request.action_type):
                return McpBackend(mcp_server)

        # Priority 2: n8n executor workflow available? (primary path)
        if webhook := await self.n8n_resolver.get_webhook(platform, request.space_id):
            return N8nBackend(webhook)

        # Priority 3: SMTP fallback for generic email?
        if platform == "smtp":
            return SmtpBackend()

        raise EgressError(
            f"No execution backend available for platform '{platform}'. "
            f"Connect {platform} in Settings > Integrations."
        )
```

---

## 5. Connection Broker

See [egress-connection-broker.md](egress-connection-broker.md) for complete details.

### Summary

The Connection Broker is the single point of contact for all credential management.
Users interact with Laya Settings only — they never need to touch n8n or any other system.

Three credential flows:

| Flow | Platforms | How It Works |
|------|-----------|-------------|
| **API Key** | Jira, GitHub, Bitbucket, Slack, Linear, Discord, GitLab, Notion | User enters credentials in Laya Settings. Broker validates, stores in keychain, provisions to n8n. |
| **OAuth** | Gmail, Google Calendar, Microsoft 365 | User clicks "Connect" in Laya Settings. Broker initiates OAuth dance, handles callback, stores refresh token, provisions to n8n. User never sees n8n. |
| **SMTP/IMAP** | ProtonMail, Fastmail, Yahoo, iCloud, Zoho, etc. | User enters SMTP/IMAP settings (auto-detected from email domain). Broker validates connection, stores in keychain. Routes to SMTP backend. |

---

## 6. Chat-Driven Egress

See [egress-chat-tools.md](egress-chat-tools.md) for complete tool definitions and interaction patterns.

### Summary

New LLM tools are added to the chat pipeline so users can trigger egress actions via
natural language. The tools follow a **preview → confirm → execute** pattern for safety.

**Egress tools**: `send_email`, `comment_on_ticket`, `transition_ticket`, `create_ticket`,
`pr_action`, `send_slack_message`, `open_compose`, `confirm_egress`

**Interaction patterns**:

1. **Direct command**: "Approve PR 23" → LLM finds PR, shows preview, user confirms, executes
2. **Smart resolution**: "Close PAY-89" → PAY-89 doesn't exist → LLM suggests PAY-98
3. **Open compose**: "Reply to Sarah's email" → LLM opens compose editor pre-filled with context
4. **Cross-platform**: "Close PROJ-123 and tell Sarah on Slack" → Two egress actions in sequence

---

## 7. Card UI Integration

### 7.1 Dynamic Quick-Action Bar

Cards query `egress.get_capabilities(platform)` to show only actions that are actually
available and for which credentials are configured:

```
Email card (Gmail connected):     [Reply] [Forward] [Archive] [Star]
Email card (Gmail NOT connected): [View in Gmail ->]  (external link only)

Jira card (Jira connected):      [Comment] [Transition v] [Assign v]
PR card (BB connected):          [Approve] [Decline] [Comment] [Merge v]
Slack card (Slack connected):    [Reply in Thread] [React]
```

### 7.2 Inline Reply/Comment Editor

When a user clicks "Reply" on an email card or "Comment" on a Jira card, an inline
editor expands within the card detail panel:

- Pre-populated with the LLM's draft (from `staged_output`)
- Editable rich text area (basic formatting: bold, italic, links, code)
- Platform-specific fields shown (To/CC for email, visibility for Jira)
- Attachment drop zone for email platforms
- Send/Post button calls `egress.execute()` — same API as chat

### 7.3 Compose Modal

A global compose button (keyboard shortcut `C`) opens a platform-agnostic compose modal:

- Platform selector at the top (Gmail, Slack, Jira, GitHub, etc.)
- Form fields change based on selected platform
- AI Assist button to get an LLM-drafted response
- The `open_compose` chat tool sends a WebSocket event that opens this same modal pre-filled

### 7.4 Action Confirmation Preview

Before any egress action executes, the UI shows a confirmation using data from
`egress.preview()`:

- Shows exactly what will be sent (recipient, content, platform)
- Warnings for high-impact actions ("This will merge 47 commits into main")
- Cancel / Confirm buttons

---

## 8. Relationship to Existing Systems

### 8.1 Stager Prompt Updates

The stager prompt (`llm/prompts/stager.py`) currently defines what actions to suggest per
platform. This needs to be updated to:

- Reference the capabilities from `egress.get_capabilities()` instead of hardcoded lists
- Include new action types (forward, archive, transition, merge, etc.)
- Generate richer payloads now that more actions are supported

### 8.2 MCP Server Extension

The existing MCP server (`mcp/server.py`) exposes Laya's tools to external MCP clients.
Egress tools should be added here too, so Claude Desktop / VS Code can trigger platform
actions through Laya.

### 8.3 WebSocket Events

New WebSocket event types:

- `open_compose` — Sent by the `open_compose` chat tool to open the compose modal in the UI
- `egress_preview` — Sent when an egress action is previewed (for confirmation UI)
- `connection_status` — Sent when a platform connection changes state

### 8.4 Pipeline Integration

The egress module is consumed at two points in the pipeline:

1. **Stager** (read-only): Queries `get_capabilities()` to know what actions to suggest
2. **Executor** (write): Calls `execute()` when user approves a suggested action

---

## 9. Implementation Roadmap

### Phase 1: Egress Module Foundation + Missing Executors

Build the module skeleton and the 3 most critical missing n8n workflows.

1. Create `engine/laya/egress/` package (models, router, n8n backend, public API)
2. Refactor `pipeline/executor.py` to delegate to egress module
3. Create Connection Broker (migrate from `connections_api.py`)
4. Build `jira-executor.json` n8n workflow (comment, transition, create issue)
5. Build `slack-executor.json` n8n workflow (send message, reply to thread)
6. Build `bitbucket-executor.json` n8n workflow (comment, approve, decline, merge PR)
7. Extend `github-executor.json` (add: approve PR, merge PR, create issue, PR comments)
8. Create platform capability registry

### Phase 2: Chat-Driven Egress

Make all egress actions available as LLM tools.

9. Add egress tool definitions (`egress/tools.py`)
10. Add egress tool handlers (`egress/tool_handlers.py`)
11. Wire into chat pipeline (`get_all_tool_definitions()` includes egress tools)
12. Implement preview/confirm two-phase flow
13. Implement `open_compose` WebSocket bridge
14. Implement `confirm_egress` tool with signed execute tokens

### Phase 3: OAuth Proxy + Setup Simplification

Ensure users never need to touch n8n for credential setup.

15. Build OAuth flow endpoints (`/egress/connections/oauth/start`, `/oauth/callback`)
16. Register Laya OAuth apps with Google and Microsoft
17. Token refresh scheduler (background task)
18. Update Settings UI — unified "Connect" button per platform
19. Auto-detect SMTP settings from email domain (well-known providers)

### Phase 4: Card UI Enhancements

Rich interaction directly on cards.

20. Dynamic quick-action bar based on `get_capabilities()`
21. Inline reply/comment editor (Svelte component)
22. Global compose modal with platform selector
23. Action confirmation preview modal
24. File attachment upload (multipart form → payload)

### Phase 5: Expansion + Polish

25. Extend Gmail executor (forward, archive, label, star, attachments)
26. Build SMTP backend for generic email
27. Build `linear-executor.json` n8n workflow
28. @-mention autocomplete (platform user search APIs)
29. MCP backend support (opt-in, for platforms with MCP servers)
30. Cross-platform action chains ("close Jira + notify Slack")

---

## 10. Key Files Reference

| File | Current Role | Post-Egress Role |
|------|-------------|------------------|
| `pipeline/executor.py` | Builds n8n payloads, calls webhooks | Thin adapter: delegates to `egress.execute()` |
| `api/actions_api.py` | REST endpoints for action execution | Unchanged — still the API surface |
| `api/connections_api.py` | Creates n8n credentials | Replaced by `egress/connections.py` (Connection Broker) |
| `integrations/platforms.py` | Platform credential schemas | Consumed by Connection Broker for field definitions |
| `integrations/n8n_client.py` | n8n REST API client | Consumed by `egress/backends/n8n.py` and Connection Broker |
| `llm/tools/definitions.py` | Chat tool definitions | Extended with `get_egress_tool_definitions()` |
| `llm/tools/executor.py` | Chat tool execution | Extended with egress tool handlers |
| `llm/prompts/stager.py` | Stager prompt with action schemas | Updated to reference capability registry |
| `mcp/server.py` | MCP tool exposure | Extended with egress tools |
| `config.py` | n8n webhook config | Consumed by egress module for webhook resolution |
