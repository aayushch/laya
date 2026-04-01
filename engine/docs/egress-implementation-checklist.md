# Egress Implementation Checklist

> Detailed, trackable checklist of every file, module, workflow, and component
> to be built. Organized by phase with dependencies noted.
> Part of the [Egress Architecture](egress-architecture.md).
>
> **Last updated**: 2026-03-31

---

## Phase 1: Egress Module Foundation + Missing Executors

### 1.1 Egress Package Skeleton

- [x] **`engine/laya/egress/__init__.py`** — Public API: `execute()`, `preview()`, `get_capabilities()`, `list_connections()`, `connect()`, `disconnect()`
- [x] **`engine/laya/egress/models.py`** — `EgressRequest`, `EgressResult`, `EgressPreview`, `EgressCapability`, `Connection`, `ConnectionResult`
- [x] **`engine/laya/egress/router.py`** — Backend resolution (n8n > SMTP), preview builder with platform-specific summaries and warnings
- [x] **`engine/laya/egress/registry.py`** — Platform capability matrix: 10 platforms, 38 action types

### 1.2 Backends

- [x] **`engine/laya/egress/backends/__init__.py`**
- [x] **`engine/laya/egress/backends/base.py`** — Abstract `EgressBackend` interface
- [x] **`engine/laya/egress/backends/n8n.py`** — n8n webhook backend (migrated from pipeline/executor.py)

### 1.3 Platform Payload Builders

- [x] **`engine/laya/egress/platforms/gmail.py`** — Body normalization, Re:/Fwd: prefixing, CC/BCC
- [x] **`engine/laya/egress/platforms/jira.py`** — issue_key normalization, comment field mapping, create defaults
- [x] **`engine/laya/egress/platforms/github.py`** — owner/repo#number parsing, int coercion, merge_method default
- [x] **`engine/laya/egress/platforms/bitbucket.py`** — workspace/repo/pr_id parsing, merge_strategy default
- [x] **`engine/laya/egress/platforms/slack.py`** — Channel # stripping, message normalization
- [x] **`engine/laya/egress/platforms/outlook.py`** — Conversation threading, Re: prefix
- [x] **`engine/laya/egress/platforms/linear.py`** — issue_id/team_id normalization, body from comment
- [x] **`engine/laya/egress/platforms/calendar.py`** — Title from summary, datetime field normalization

### 1.4 Connection Broker

- [x] **`engine/laya/egress/connections.py`** — Full broker: `create_connection()`, `remove_connection()`, `list_all_connections()`, `test_connection()` with credential validation for 8 platform types (Jira, GitHub, Bitbucket, Slack, Linear, GitLab, Discord, SMTP)
- [x] **`engine/laya/db/migrations/034_egress_connections.sql`** — `egress_connections` table with indexes

### 1.5 Refactor Executor

- [x] **`engine/laya/pipeline/executor.py`** — Refactored to delegate to `egress.execute()`. Engine only handles card lifecycle.

### 1.6 API Routes

- [x] **`engine/laya/api/egress_api.py`** — REST endpoints: execute, preview, capabilities, connections CRUD, test, email detection, OAuth start/callback/setup
- [x] **`engine/laya/main.py`** — Egress router mounted + health monitor wired into lifecycle

### 1.7 n8n Executor Workflows (NEW)

- [x] **`n8n/workflows/jira-executor.json`** — 4 routes: comment, transition (with dynamic transition ID resolution), create_issue, assign
- [x] **`n8n/workflows/slack-executor.json`** — 3 routes: send_message, reply_thread, react
- [x] **`n8n/workflows/bitbucket-executor.json`** — 4 routes: comment_pr, approve_pr, decline_pr, merge_pr

### 1.8 Extend Existing n8n Workflows

- [x] **`n8n/workflows/github-executor.json`** — Extended to 6 routes: close_issue, comment, approve_pr, request_changes, merge_pr, create_issue

### 1.9 Config Updates

- [x] **`engine/laya/config.py`** — Added google_calendar, linear webhook entries

### 1.10 Tests

- [x] **`engine/tests/test_egress_models.py`** — 15 tests for all data models
- [x] **`engine/tests/test_egress_router.py`** — 20 tests for preview summaries, warnings, impact levels
- [x] **`engine/tests/test_egress_n8n_backend.py`** — 16 tests for payload building, webhook resolution, execution
- [x] **`engine/tests/test_egress_platforms.py`** — 48 tests for all 8 platform payload builders
- [x] **`engine/tests/test_egress_connections.py`** — 15 tests for credential validation, connection CRUD

**Total: 114 tests, all passing.**

---

## Phase 2: Chat-Driven Egress

### 2.1 Tool Definitions

- [x] **`engine/laya/egress/tools.py`** — 8 tool definitions: `send_email`, `comment_on_ticket`, `transition_ticket`, `create_ticket`, `pr_action`, `send_slack_message`, `open_compose`, `confirm_egress`

### 2.2 Tool Handlers

- [x] **`engine/laya/egress/tool_handlers.py`** — Preview/confirm flow with HMAC-signed execute tokens (5-min TTL), request builders for all 6 action tools, open_compose WebSocket bridge

### 2.3 Wire Into Chat Pipeline

- [x] **`engine/laya/llm/tools/definitions.py`** — Egress tools included (30 total tools)
- [x] **`engine/laya/llm/tools/executor.py`** — Egress tool routing added
- [x] **`engine/laya/llm/prompts/chat.py`** — Egress guidance in system prompt (6 rules)

### 2.4 MCP Server Extension

- [x] **`engine/laya/mcp/server.py`** — Already uses `get_all_tool_definitions()` which includes egress tools. No changes needed.

### 2.5 WebSocket Events

- [x] **`open_compose`** — Broadcast from tool_handlers.py, received in +layout.svelte
- [x] **`connection_status`** — Broadcast from health.py on status changes

### 2.6 Preview Implementation

- [x] **`egress/router.py:build_preview()`** — Platform-specific summaries for all platforms, warnings for destructive actions, impact levels (low/medium/high)

---

## Phase 3: OAuth Proxy + Setup Simplification

### 3.1 OAuth Flow

- [x] **`engine/laya/egress/oauth.py`** — Full OAuth module: `build_auth_url()`, `handle_callback()`, `refresh_access_token()`, CSRF state management
- [x] **OAuth endpoints in `egress_api.py`** — `/oauth/start`, `/oauth/callback`, `/oauth/setup`
- [x] **OAuth provider configs** — Google (Gmail + Calendar scopes), Microsoft (Outlook + Calendar scopes)
- [x] **Token exchange + storage** — Auth code → access + refresh tokens, stored in keychain, provisioned to n8n
- [x] **`store_oauth_client()`** — Store OAuth app credentials (client_id/secret) per platform

### 3.2 Token Refresh

- [x] **`egress/oauth.py:refresh_access_token()`** — Exchange refresh token for new access token, update keychain + n8n credential
- [x] **`egress/health.py:_check_oauth_token()`** — Checks expiry, auto-refreshes 10 min before

### 3.3 SMTP Auto-Detection

- [x] **`GET /egress/connections/detect`** — Email domain lookup against 12 well-known providers
- [x] **Well-known providers** — Gmail (OAuth redirect), Outlook (OAuth redirect), ProtonMail, Fastmail, Yahoo, iCloud, Zoho, AOL + variants
- [x] **SMTP validation** — In `connections.py:_validate_smtp()` via aiosmtplib

### 3.4 Connection Health Monitoring

- [x] **`engine/laya/egress/health.py`** — Background task every 30 min, validates API-key connections, refreshes OAuth tokens, broadcasts status changes
- [x] **Wired into engine lifecycle** — `start_health_monitor()` on startup, `stop_health_monitor()` on shutdown

### 3.5 Settings UI — Unified Connection Management

- [x] **`ui/src/lib/components/settings/IntegrationsConfig.svelte`** — Complete rewrite: platform grid grouped by category, connection status, connect/test/disconnect flows
- [x] **`ui/src/lib/components/settings/PlatformCard.svelte`** — Platform card with status indicator, capability count, test/disconnect buttons
- [x] **`ui/src/lib/components/settings/ConnectModal.svelte`** — Modal with 3 modes: API-key form, OAuth flow (with polling), SMTP setup
- [x] **`ui/src/lib/components/settings/SmtpSetupForm.svelte`** — Email auto-detect + SMTP/IMAP field form
- [x] **`ui/src/lib/components/settings/N8nAdvancedSection.svelte`** — Extracted n8n management (workflows, webhooks, API key) into collapsible section
- [x] **`ui/src/lib/components/settings/PlatformIcon.svelte`** — Added smtp/email icon

### 3.6 SMTP Backend

- [x] **`engine/laya/egress/backends/smtp.py`** — MIME message building with threading headers, STARTTLS, aiosmtplib

### 3.7 Migration

- [ ] **Migrate existing n8n connections** — Script to copy existing credentials into egress_connections table + keychain
- [ ] **Deprecate old `/connections/` routes** — Mark as deprecated, update any remaining UI references

---

## Phase 4: Card UI Enhancements

### 4.1 Quick-Action Bar

- [x] **`ui/src/lib/components/egress/QuickActions.svelte`** — Dynamic action bar: fetches capabilities, platform-specific buttons, routes to InlineEditor or ConfirmAction
- [ ] **Integrate into `CardDetail.svelte`** — Add QuickActions component below card header

### 4.2 Inline Reply/Comment Editor

- [x] **`ui/src/lib/components/egress/InlineEditor.svelte`** — Textarea editor, pre-filled, platform-specific headers (To/Subject for email), send via API, success/error states

### 4.3 Global Compose Modal

- [x] **`ui/src/lib/components/egress/ComposeModal.svelte`** — Platform tabs (Gmail, Slack, Jira, GitHub), per-platform forms, AI Assist placeholder, Escape to close
- [x] **`ui/src/lib/stores/compose.ts`** — Reactive compose state store
- [x] **`ui/src/routes/+layout.svelte`** — WebSocket listener for `open_compose`, keyboard shortcut `C`, ComposeModal rendered

### 4.4 Action Confirmation Modal

- [x] **`ui/src/lib/components/egress/ConfirmAction.svelte`** — Preview display, warnings in amber, cancel/confirm with loading state

### 4.5 Attachment Upload

- [ ] **`POST /egress/attachments`** API endpoint — Multipart form upload, temporary storage, returns attachment_id
- [ ] **Attachment UI in InlineEditor and ComposeModal** — Drag-and-drop zone, file picker, progress indicator
- [ ] **Attachment inclusion in egress payload** — Base64 encode and include in EgressRequest

### 4.6 API Client Updates

- [x] **`ui/src/lib/api/engine.ts`** — All egress functions: execute, preview, capabilities, connections CRUD, test, OAuth start/setup, email detection
- [x] **`ui/src/lib/api/types.ts`** — All egress TypeScript types including `OAuthStartResponse`

---

## Phase 5: Expansion + Polish

### 5.1 Gmail Executor Extensions

- [x] **`n8n/workflows/gmail-executor.json`** — Extended to 5 routes: send_email, forward, archive, star, mark_read

### 5.2 Linear Executor

- [x] **`n8n/workflows/linear-executor.json`** — 4 routes: create_issue, comment, update_status, assign (all GraphQL)

### 5.3 @-Mention Autocomplete

- [ ] **`GET /egress/search-users?platform={platform}&query={query}`** — Platform user search API
- [ ] **Platform-specific user search** — Jira /user/search, GitHub /search/users, Slack users.list, Bitbucket /members
- [ ] **Autocomplete UI component** — `@` trigger in InlineEditor and ComposeModal

### 5.4 MCP Backend (Future)

- [ ] **`engine/laya/egress/backends/mcp.py`** — McpBackend class for platforms with MCP servers
- [ ] **MCP server registry in config** — Map platform → MCP server connection info
- [ ] **Router update** — Check MCP registry before n8n backend

### 5.5 Cross-Platform Action Chains

- [ ] **`engine/laya/egress/chains.py`** — Chain executor: sequential execution with rollback
- [ ] **`chain_actions` chat tool** — LLM proposes chain, preview all, confirm all
- [ ] **UI: Multi-action confirmation** — Shows all actions in chain

### 5.6 Template System

- [ ] **DB migration for templates table** — `template_id`, `platform`, `action_type`, `name`, `content`, `variables`
- [ ] **Template CRUD API** — Create, list, update, delete
- [ ] **Template selector in ComposeModal** — Dropdown to apply templates
- [ ] **Template variables** — `{{sender_name}}`, `{{ticket_id}}`, auto-filled

### 5.7 Scheduled Sends

- [ ] **`engine/laya/egress/scheduler.py`** — Schedule egress for future execution
- [ ] **DB table for scheduled actions** — `scheduled_egress` table
- [ ] **Background task** — Polls for due scheduled actions
- [ ] **UI: Schedule button in ComposeModal** — Date/time picker

---

## Summary

| Phase | Items | Done | Remaining |
|-------|-------|------|-----------|
| Phase 1: Foundation | 28 | 28 | 0 |
| Phase 2: Chat Egress | 10 | 10 | 0 |
| Phase 3: OAuth + Setup | 16 | 14 | 2 (migration, deprecation) |
| Phase 4: Card UI | 10 | 8 | 2 (CardDetail integration, attachments) |
| Phase 5: Expansion | 16 | 2 | 14 (autocomplete, MCP backend, chains, templates, scheduled sends) |
| **Total** | **80** | **62** | **18** |

### Remaining Items (18)

**Quick wins (can be done incrementally):**
1. Integrate QuickActions into CardDetail.svelte
2. Migrate existing n8n connections to egress_connections table
3. Deprecate old `/connections/` routes

**Medium effort (feature additions):**
4. Attachment upload API + UI
5. @-mention autocomplete API + UI

**Larger features (future roadmap):**
6-18. MCP backend, cross-platform chains, template system, scheduled sends — these are architectural extensions that build on the completed foundation.

---

## File Count Summary (Actual)

| Category | New Files | Modified Files |
|----------|-----------|----------------|
| Egress package (Python) | 22 | 0 |
| API routes | 1 | 1 (main.py) |
| DB migrations | 1 | 0 |
| n8n workflows | 4 new | 2 modified |
| Chat integration | 0 | 3 (definitions.py, executor.py, chat prompt) |
| Stager prompt | 0 | 1 |
| Config | 0 | 1 (config.py) |
| Tests | 5 | 0 |
| UI egress components | 5 new | 0 |
| UI settings components | 5 new | 1 (IntegrationsConfig rewrite) |
| UI stores | 1 new | 0 |
| UI API/types | 0 | 2 (engine.ts, types.ts) |
| UI layout | 0 | 1 (+layout.svelte) |
| UI icons | 0 | 1 (PlatformIcon.svelte) |
| Documentation | 5 | 0 |
| **Total** | **~49 new** | **~13 modified** |
