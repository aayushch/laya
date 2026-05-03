# Laya Implementation Plan

## Overview

This document defines the build sequence for Laya v0.1. The plan is organized into 8 milestones, each producing a testable increment.

**Target:** ~19 weeks from start to shippable v0.1.

## Milestone 1: Skeleton (Week 1-2)

**Goal:** Get all three processes running and talking to each other.

### Deliverables

- [x] **Tauri app** launches and shows a blank Svelte page with Skeleton UI + Tailwind
- [x] **Python engine** starts as a Tauri sidecar process, serves FastAPI on `localhost:8420`
- [x] **n8n** installs via npm and starts as a local Node.js process
- [x] WebSocket connection established between Svelte UI and FastAPI engine
- [x] `POST /events` endpoint accepts a JSON payload and returns 202
- [x] `GET /health` endpoint returns status of engine, n8n, SQLite
- [x] SQLite database created at `~/.laya/data/laya.db` with initial schema (001_initial.sql)
- [x] `~/.laya/` directory structure created on first launch
- [x] Basic logging to `~/.laya/logs/laya.log` via structlog
- [x] System tray icon (static, no badge yet)

### Test Criteria

Manually POST a JSON event to `/events` via curl. Confirm it's stored in SQLite. Confirm a "raw event received" message appears in the Svelte UI via WebSocket.

### Key Technical Decisions

- Engine bundling: Bundle Python source into Tauri app resources. Tauri creates a managed venv at `~/.laya/venv/` and installs dependencies on first launch.
- n8n management: Tauri installs n8n via npm and manages it as a local Node.js process on port 45678.
- FastAPI + uvicorn running from the managed venv.

---

## Milestone 2: Ingestion Pipeline (Week 3-4)

**Goal:** n8n receives real events from all sources and normalizes them to the Laya Event schema.

### Deliverables

- [x] **n8n Jira ingestion workflow:** Jira trigger -> normalize to Laya Event -> POST to engine
- [x] **n8n Slack ingestion workflow:** Slack event trigger -> normalize -> POST
- [x] **n8n Gmail ingestion workflow:** Gmail trigger -> normalize -> POST
- [x] **n8n Bitbucket ingestion workflow:** Bitbucket webhook -> normalize -> POST
- [x] **n8n Calendar ingestion workflow:** Google Calendar trigger -> normalize -> POST
- [x] **n8n Outlook email/calendar ingestion workflows:** Outlook trigger -> normalize -> POST
- [x] **n8n Linear ingestion workflow:** Linear trigger -> normalize -> POST
- [x] **n8n Notion ingestion workflow:** Notion trigger -> normalize -> POST
- [x] All workflow JSONs stored in `n8n/workflows/` and auto-importable (10 ingestion + 10 executor + 1 error handler)
- [x] Laya Event schema validation in engine (Pydantic model)
- [x] **INGEST node:** Parse event, resolve actor relationship via team.json, store in SQLite
- [x] **RULES ENGINE node:** Basic drop/pass filtering against rules.json
- [x] Config file loading: `settings.json`, `team.json`, `rules.json`
- [x] Settings page (Svelte): Team editor and rules editor (basic CRUD UI)

### Test Criteria

Create a real Jira ticket assigned to the user. Confirm:
1. n8n catches the webhook
2. Event appears in SQLite with correct Laya Event schema
3. Actor relationship resolved from team.json
4. A filtered event (matching a drop rule) is logged but not processed

---

## Milestone 3: Router + Classification (Week 5-6)

**Goal:** The LLM classifies events with category, persona, priority, and entities.

### Deliverables

- [x] **LiteLLM integration** with configurable model (settings.json)
- [x] **LLM client wrapper** (`laya/llm/client.py`) handling model selection, retries, error handling
- [x] **Router prompt template** (`laya/llm/prompts/router.py`)
- [x] **ROUTER node:** Call LLM to classify category, persona, priority
- [x] **ROUTER node:** Extract entities (ticket IDs, file paths, people mentions)
- [x] **ROUTER node:** Generate research_plan (list of investigation steps)
- [x] **ROUTER node:** Determine `requires_research` flag and `secondary_persona`
- [x] **ChromaDB integration** (embedded mode, local nomic-embed via sentence-transformers)
- [x] **memory_search tool:** Semantic similarity search on past events
- [x] **ROUTER node:** Query ChromaDB for related past events, inject as context
- [x] **Entity resolution (Layer 1):** Extract explicit cross-references from Router output, store in entities table
- [x] Settings page: Model configuration UI (router model, stager model, local model)
- [x] **OS keychain integration** for API key storage (macOS Keychain first, then Linux/Windows)

### Test Criteria

POST a Jira bug event. Confirm:
1. Router classifies as `category=CODE, persona=ENGINEER, priority=CRITICAL`
2. Entities extracted: ticket ID, mentioned file paths
3. Research plan generated with 3-5 investigation steps
4. Classification stored in SQLite, event content embedded in ChromaDB
5. A second similar event retrieves the first as related context

---

## Milestone 4: Workers + Coding Agent (Week 7-9)

**Goal:** Workers perform research. The ENGINEER worker orchestrates interactive coding agent sessions.

### Deliverables

- [x] **CodingAgent protocol** (`agents/base.py`): `start_session()`, `stream_events()`, `send_input()`, `pause()`, `resume()`, `cancel()`
- [x] **AgentSession** implementation using asyncio + PTY subprocess management
- [x] **Claude Code adapter** (`agents/claude_code.py`): spawn `claude -p`, parse output, intercept approval prompts
- [x] **Gemini CLI adapter** (`agents/gemini_cli.py`): same interface
- [x] **Codex CLI adapter** (`agents/codex_cli.py`): same interface
- [x] **Session manager** (`agents/session_manager.py`): track active sessions across cards, handle lifecycle
- [x] **ENGINEER Worker:**
  - Gather internal context (memory_search, entity_lookup, card_history)
  - Build prompt with research_plan + gathered context
  - Spawn coding agent PTY session in configured repo directory
  - Stream agent progress events to WebSocket
  - Intercept agent approval requests, surface via WebSocket
  - Pipe user responses from WebSocket back to agent stdin
  - Parse structured findings on agent completion
- [x] **COMMS Worker:** Draft replies using LLM + memory context (no coding agent needed)
- [x] **OPS Worker:** Calendar prep briefings using event history + LLM synthesis
- [x] **FINANCE Worker:** Invoice, expense, and budget event processing
- [x] **HR Worker:** People ops, onboarding, leave request processing
- [x] **SALES Worker:** Pipeline, deal, and prospect tracking
- [x] **Sequential multi-worker execution:** When Router specifies secondary_persona, run workers in sequence, passing findings forward
- [x] **Workspace state persistence:**
  - `workspace_sessions` table in SQLite
  - `workspace_events` table in SQLite
  - All agent messages, user responses, tool calls logged as workspace events
- [x] Settings page: Coding agent selection UI + repo configuration UI
- [x] `repos.json` config file loading

### Test Criteria

1. Jira bug event -> ENGINEER Worker invokes Claude Code on a local test repo
2. Agent researches, reads files, runs git commands
3. Agent asks "Modify 3 files?" -> approval request appears in UI via WebSocket
4. User approves via WebSocket -> agent continues
5. Agent completes -> structured findings JSON returned
6. Navigate away during agent session, come back -> workspace state restored from SQLite
7. Slack mention event -> COMMS Worker drafts a reply without coding agent
8. Calendar event -> OPS Worker generates a meeting prep brief

---

## Milestone 5: Stager + Action Cards (Week 10-11)

**Goal:** Produce polished Action Cards with actionable outputs and display them in the feed.

### Deliverables

- [x] **Stager prompt template** (`laya/llm/prompts/stager.py`)
- [x] **STAGER node:** Synthesize worker findings + context into polished Action Card JSON
- [x] **STAGER node:** Generate `suggested_actions` array (multiple possible actions per card)
- [x] **STAGER node:** Include intelligence_report, staged_output, privacy tier indicator
- [x] **EMIT node:**
  - Store Action Card in SQLite (action_cards table)
  - Embed card summary in ChromaDB
  - Update entity cross-references (entity_link)
  - Log to audit_log (model used, tokens, latency, tier)
  - Push card to UI via WebSocket (`card_created` message)
  - Trigger context association and group summary
- [x] **Entity resolution (Layer 2):** Semantic matching via ChromaDB similarity
- [x] **Entity resolution (Layer 3):** LLM confirmation before creating new entity links
- [x] **Feed UI (Svelte):**
  - Action Card component (compact view in feed)
  - Card list with scroll + Card view with 3-column grid
  - Priority badges (CRITICAL/HIGH/MEDIUM/LOW)
  - Status indicators (pending, awaiting_input, agent_running, staged, etc.)
  - Filter bar (by status, priority, source, persona, space)
  - Sort controls (priority+time, newest, oldest, category, platform)
- [x] **Simple card approval:** One-click approve from feed for simple cards (Slack reply, calendar prep)
- [x] **Card dismiss** with optional reason
- [x] Card status transitions: `pending -> ready -> requires_approval -> agent_running -> awaiting_input -> done | failed | dismissed | archived`
- [x] **Privacy tier indicator** on cards (visual marker for Tier 3 events)
- [x] **Learning loop (storage):** Record approval/edit/dismiss decisions in action_cards table
- [x] **Feedback query tool:** Query past approval patterns for similar event types

### Test Criteria

Full end-to-end: Jira ticket created -> n8n fires -> Engine classifies -> Worker researches -> Stager produces card -> Card appears in feed with intelligence report, staged fix, and action buttons. User clicks "Approve" -> card status changes to "approved".

---

## Milestone 6: Execution + Workspace UI (Week 12-14)

**Goal:** Approved actions execute via n8n. Complex cards open full workspaces.

### Deliverables

- [x] **n8n execution workflows** (10 platforms):
  - jira-executor: comment, transition, create_issue, assign
  - bitbucket-executor: comment_pr, approve_pr, decline_pr, merge_pr
  - slack-executor: send_message, reply_thread, react
  - gmail-executor: send_email, forward, archive, star, mark_read
  - calendar-executor: create/update/delete events (Google + Outlook)
  - github-executor: close_issue, comment, approve_pr, request_changes, merge_pr, create_issue
  - linear-executor: create_issue, comment, update_status, assign
  - outlook-email/calendar-executor: send, reply, calendar ops
  - notion-executor: create_page, update_page
- [x] **Engine -> n8n action forwarding:** POST approved action payload to n8n webhook via egress module
- [x] **Execution result handling:** Parse n8n response, update card status (completed/failed), store in action_log
- [x] **Card status update via WebSocket** (`card_updated` message with result URL)
- [x] **Workspace UI (Svelte):**
  - Workspace layout (three panels: timeline + live agent + context/staged)
  - Timeline component: chronological event display
  - Live Agent panel: streaming agent output, approval prompts, user input
  - Context sidebar: related entities, cross-platform references, team info
  - Staged Outputs panel: code diffs, drafted emails, PR descriptions
  - Code diff component with syntax highlighting
  - Session controls: pause, resume, cancel
- [x] **Workspace state restoration:** Navigate away and back, full state loads from SQLite
- [x] **Feed status badges:** Visual indicators (awaiting input, agent running, staged, etc.)
- [x] **Native notifications** via Tauri for HIGH/CRITICAL cards
- [x] **System tray badge:** Count of pending cards requiring attention

### Test Criteria

1. Full loop: Jira ticket -> card -> open workspace -> interact with coding agent -> approve staged fix -> PR created in Bitbucket -> card status shows "completed" with PR URL
2. Navigate away mid-session, come back -> workspace fully restored
3. Approve a simple Slack reply card from feed -> message sent in Slack
4. Native notification fires for CRITICAL card

---

## Milestone 7: Dashboard, Chat, Briefing, Learning (Week 15-16)

**Goal:** Complete the v0.1 feature set with analytics, chat, daily briefing, and feedback-based learning.

### Deliverables

- [x] **Dashboard UI (Svelte):**
  - Stat cards: events processed, cards generated, approved/edited/dismissed, pending
  - Estimated time saved (running total with configurable estimates per action type)
  - LLM cost tracking (from audit_log token counts + per-model pricing)
  - Feature cost chart with expandable step-level details
  - Approval rate by persona chart
  - Average response time
- [x] **Dashboard API:** `GET /dashboard` endpoint with aggregation queries on SQLite
- [x] **Chat sidebar UI (Svelte):**
  - Chat panel with message history
  - Chat input with send button
  - Messages display with Laya responses and referenced cards/events
- [x] **Chat pipeline (asyncio):**
  - Parse intent step (fast LLM)
  - Retrieve context step (ChromaDB + SQLite)
  - Respond step (strong LLM with tool calling)
  - Reference specific cards and events in responses
  - find_contact tool for people lookup
- [x] **Chat API:** WebSocket `chat_message` type + `POST /chat` REST fallback
- [x] **Daily Briefing:**
  - Scheduler job (configurable time via settings.json)
  - Briefing worker: query overnight events, pending cards, today's calendar
  - Briefing stager: synthesize into briefing card with meeting prep context
  - Native notification: "Your morning briefing is ready"
- [x] **Learning loop (prompt injection):**
  - feedback_query tool returns recent approval/edit/dismiss patterns
  - Router prompt includes classification rules learned from correction patterns
  - Context learning extracts grouping rules from link/unlink corrections
- [x] **Audit log viewer** in settings page (filterable table)

### Test Criteria

1. Dashboard shows accurate numbers matching SQLite data
2. Chat correctly answers "What happened with BUG-1234?" by finding related events and cards
3. Daily briefing generates at configured time with overnight summary + calendar context
4. After dismissing 5 low-priority Slack events, Router starts classifying similar events as lower priority

---

## Milestone 8: Polish, Packaging, Testing (Week 17-19)

**Goal:** Ship a production-quality v0.1 across all three platforms.

### Deliverables

- [x] **First-run setup wizard (Svelte):**
  - Step 1: LLM configuration (model selection, API key entry -> keychain)
  - Step 2: n8n check (detect Node.js, install n8n, auto-configure)
  - Step 3: Coding agent selection + repo directory picker
  - Step 4: Team member entry (name, email, role)
  - Step 5: Event filter presets (ignore bots, ignore status changes, etc.)
- [x] **Engine source bundling:** Python source bundled into Tauri app resources, venv created at `~/.laya/venv/` on first launch
- [x] **Tauri engine lifecycle:** Tauri manages Python venv creation, dependency installation, and engine process start/stop
- [x] **n8n management from Tauri:** Install via npm, start/stop n8n process, two-attempt install with native addon fallback
- [ ] **Platform-specific installers:**
  - macOS: DMG with drag-to-Applications, code signed
  - Linux: AppImage + .deb package
  - Windows: MSI installer
- [ ] **Tauri auto-update** configuration (stable/beta channels, update check URL)
- [ ] **System tray:** Health status indicator (green/yellow/red), badge count, quick actions
- [x] **OS keychain integration** verified on all three platforms
- [x] **Error handling throughout:**
  - n8n offline: show indicator, queue events, retry on reconnect
  - LLM API timeout: retry with backoff, show "processing delayed" on card
  - Coding agent crash: mark session as failed, preserve workspace state, show error
  - Invalid event: log validation error, track in ingestion_errors table, don't crash
- [x] **Graceful shutdown:** Save workspace states, stop agent sessions, stop n8n process
- [x] **Database migrations** verified: fresh install and upgrade from earlier milestone builds (59 migrations)
- [ ] **Test suites:**
  - Python unit tests (pytest): all graph nodes, tools, rules engine, models
  - Python integration tests: full event flow with mocked LLM, workspace persistence
  - Svelte component tests (Vitest): all major components
  - E2E tests (Playwright): setup wizard, event-to-card flow, workspace interaction, chat
- [ ] **Documentation:** Setup guide for users, developer setup guide
- [x] **n8n workflow import script** verified on all platforms
- [x] **Export Diagnostics** button: bundle logs + config (redacted) + system info into zip

### Test Criteria

1. Clean install on fresh macOS, Linux, and Windows machines
2. Setup wizard completes end-to-end
3. Events flow from all 10 platform sources
4. Cards generate with intelligence and staged outputs
5. Workspaces work with interactive agent sessions
6. Actions execute via n8n
7. Dashboard, chat, and briefing all functional
8. No crashes during 24-hour soak test
9. Update from a previous milestone build succeeds with database migration

---

## Milestone 9: Omni — Rolling Cross-Platform Summary

**Goal:** Provide a single unified view that answers "where am I right now?" across all platforms and spaces.

### Deliverables

- [x] **Database schema** (`039_omni.sql`): `omni_snapshots` and `omni_pins` tables with space isolation and version indexing
- [x] **Pydantic models** (`models/omni.py`): OmniItem, OmniSection, OmniSnapshot, OmniPin, OmniStats
- [x] **LLM prompts** (`llm/prompts/omni.py`): System prompt for cross-cutting synthesis, density presets (compact/standard/detailed), JSON schema for structured output, resynthesis message builder
- [x] **Pipeline** (`pipeline/omni.py`):
  - `trigger_omni_update()`: Debounced (10s) incremental append to Recent layer — no LLM cost
  - `run_omni_resynthesis()`: Full LLM resynthesis compressing all four temporal layers
  - User-acted cards receive higher weight during compression
  - Pinned items preserved exactly as written
- [x] **Pipeline integration**: `emit.py` calls `trigger_omni_update()` at step 9 of card creation
- [x] **Scheduler integration**: Daily resynthesis at configurable time (default 17:00, timezone-aware)
- [x] **API** (`api/omni_api.py`):
  - `GET /omni` — Latest or specific version snapshot
  - `GET /omni/history` — Version list for time-slider
  - `POST /omni/resynthesis` — Manual trigger
  - `GET /omni/pins`, `POST /omni/pin`, `DELETE /omni/pin/:pin_id` — Pin CRUD
- [x] **WebSocket**: `omni_updated` message broadcast after resynthesis
- [x] **UI Components** (`components/omni/`):
  - `OmniView.svelte` — Four sections with icons, item counts, empty states
  - `OmniHeader.svelte` — Title, version slider, refresh button, stats bar
  - `OmniItem.svelte` — Priority dot, text, platform badges, card links, pin toggle
- [x] **UI Routes**:
  - `/omni` — Main page with space selector, loading states, WebSocket auto-reload
  - `/omni/insight` — Drill-down: card content panels + resizable contextual chat
- [x] **Tests** (`tests/test_omni_api.py`): 8 tests covering snapshots, pins, space isolation, version retrieval

### Test Criteria

1. New cards arrive -> incremental items appear in Recent layer without LLM call
2. Manual resynthesis compresses layers and produces a versioned snapshot
3. Pinned items survive resynthesis exactly as written
4. Version slider navigates between historical snapshots
5. Drill-down opens source cards in the Insight view with contextual chat
6. Different spaces maintain independent summaries

---

## Post-v0.1 Roadmap

### v0.2 (Planned)
- Additional personas: Sales/CRM, HR/Ops, Finance
- Focus Mode (suppress low-priority during deep work)
- PII detection (regex-based)
- ML-based learning from approval patterns
- n8n workflow customization UI within Laya

### v0.3+ (Future)
- Custom persona builder (user-defined personas with custom prompts/tools)
- Workflow templates (shareable automation recipes)
- Plugin system for third-party tool integrations
- Advanced analytics and reporting

### Explicitly Out of Scope
- Multi-user / team mode (contradicts local-first single-user architecture)
- Cloud-hosted version (may revisit as separate product)
