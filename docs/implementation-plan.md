# Laya Implementation Plan

## Overview

This document defines the build sequence for Laya v0.1. The plan is organized into 8 milestones, each producing a testable increment.

**Target:** ~19 weeks from start to shippable v0.1.

## Milestone 1: Skeleton (Week 1-2)

**Goal:** Get all three processes running and talking to each other.

### Deliverables

- [x] **Tauri app** launches and shows a blank Svelte page with Skeleton UI + Tailwind
- [x] **Python engine** starts as a Tauri sidecar process, serves FastAPI on `localhost:8420`
- [x] **n8n Docker container** starts via docker-compose
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

- Engine bundling: Use PyInstaller for the sidecar binary. Ensure it works on macOS, Linux, Windows.
- Docker management: Use Tauri's shell command API to run `docker start/stop`.
- FastAPI + uvicorn running inside the PyInstaller bundle.

---

## Milestone 2: Ingestion Pipeline (Week 3-4)

**Goal:** n8n receives real events from all 5 sources and normalizes them to the Laya Event schema.

### Deliverables

- [x] **n8n Jira ingestion workflow:** Jira trigger -> normalize to Laya Event -> POST to engine
- [x] **n8n Slack ingestion workflow:** Slack event trigger -> normalize -> POST
- [x] **n8n Gmail ingestion workflow:** Gmail trigger -> normalize -> POST
- [x] **n8n Bitbucket ingestion workflow:** Bitbucket webhook -> normalize -> POST
- [x] **n8n Calendar ingestion workflow:** Google Calendar trigger -> normalize -> POST
- [x] All 5 workflow JSONs stored in `n8n/workflows/` and auto-importable
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

- [ ] **CodingAgent protocol** (`agents/base.py`): `start_session()`, `stream_events()`, `send_input()`, `pause()`, `resume()`, `cancel()`
- [ ] **AgentSession** implementation using asyncio + PTY subprocess management
- [ ] **Claude Code adapter** (`agents/claude_code.py`): spawn `claude -p`, parse output, intercept approval prompts
- [ ] **Gemini CLI adapter** (`agents/gemini_cli.py`): same interface
- [ ] **Codex CLI adapter** (`agents/codex_cli.py`): same interface
- [ ] **Session manager** (`agents/session_manager.py`): track active sessions across cards, handle lifecycle
- [ ] **ENGINEER Worker:**
  - Gather internal context (memory_search, entity_lookup, card_history)
  - Build prompt with research_plan + gathered context
  - Spawn coding agent PTY session in configured repo directory
  - Stream agent progress events to WebSocket
  - Intercept agent approval requests, surface via WebSocket
  - Pipe user responses from WebSocket back to agent stdin
  - Parse structured findings on agent completion
- [ ] **COMMS Worker:** Draft replies using LLM + memory context (no coding agent needed)
- [ ] **OPS Worker:** Calendar prep briefings using event history + LLM synthesis
- [ ] **Sequential multi-worker execution:** When Router specifies secondary_persona, run workers in sequence, passing findings forward
- [ ] **Workspace state persistence:**
  - `workspace_sessions` table in SQLite
  - `workspace_events` table in SQLite
  - All agent messages, user responses, tool calls logged as workspace events
- [ ] Settings page: Coding agent selection UI + repo configuration UI
- [ ] `repos.json` config file loading

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

- [ ] **Stager prompt template** (`laya/llm/prompts/stager.py`)
- [ ] **STAGER node:** Synthesize worker findings + context into polished Action Card JSON
- [ ] **STAGER node:** Generate `suggested_actions` array (multiple possible actions per card)
- [ ] **STAGER node:** Include intelligence_report, staged_output, privacy tier indicator
- [ ] **EMIT node:**
  - Store Action Card in SQLite (action_cards table)
  - Embed card summary in ChromaDB
  - Update entity cross-references (entity_link)
  - Log to audit_log (model used, tokens, latency, tier)
  - Push card to UI via WebSocket (`card_created` message)
- [ ] **Entity resolution (Layer 2):** Semantic matching via ChromaDB similarity
- [ ] **Entity resolution (Layer 3):** LLM confirmation before creating new entity links
- [ ] **Feed UI (Svelte):**
  - Action Card component (compact view in feed)
  - Card list with scroll
  - Priority badges (CRITICAL/HIGH/MEDIUM/LOW)
  - Status indicators (pending, awaiting_input, agent_running, staged, etc.)
  - Filter bar (by status, priority, source, persona)
  - Sort controls (priority+time, newest, oldest)
- [ ] **Simple card approval:** One-click approve from feed for simple cards (Slack reply, calendar prep)
- [ ] **Card dismiss** with optional reason
- [ ] Card status transitions: `pending -> reviewing -> approved -> executing -> completed`
- [ ] **Privacy tier indicator** on cards (visual marker for Tier 3 events)
- [ ] **Learning loop (storage):** Record approval/edit/dismiss decisions in action_cards table
- [ ] **Feedback query tool:** Query past approval patterns for similar event types

### Test Criteria

Full end-to-end: Jira ticket created -> n8n fires -> Engine classifies -> Worker researches -> Stager produces card -> Card appears in feed with intelligence report, staged fix, and action buttons. User clicks "Approve" -> card status changes to "approved".

---

## Milestone 6: Execution + Workspace UI (Week 12-14)

**Goal:** Approved actions execute via n8n. Complex cards open full workspaces.

### Deliverables

- [ ] **n8n execution workflows** (all 5 platforms):
  - jira-executor: add comment, update ticket
  - bitbucket-executor: create PR, add PR comment
  - slack-executor: send message, reply to thread
  - gmail-executor: send email, reply to email
  - calendar-executor: create event, update event
- [ ] **Engine -> n8n action forwarding:** POST approved action payload to n8n webhook
- [ ] **Execution result handling:** Parse n8n response, update card status (completed/failed), store in action_log
- [ ] **Card status update via WebSocket** (`card_updated` message with result URL)
- [ ] **Workspace UI (Svelte):**
  - Workspace layout (three panels: timeline + live agent + context/staged)
  - Timeline component: chronological event display
  - Live Agent panel: streaming agent output, approval prompts, user input
  - Context sidebar: related entities, cross-platform references, team info
  - Staged Outputs panel: code diffs, drafted emails, PR descriptions
  - Code diff component with syntax highlighting
  - Session controls: pause, resume, cancel
- [ ] **Workspace state restoration:** Navigate away and back, full state loads from SQLite
- [ ] **Feed status badges:** Visual indicators (awaiting input, agent running, staged, etc.)
- [ ] **Native notifications** via Tauri for HIGH/CRITICAL cards
- [ ] **System tray badge:** Count of pending cards requiring attention

### Test Criteria

1. Full loop: Jira ticket -> card -> open workspace -> interact with coding agent -> approve staged fix -> PR created in Bitbucket -> card status shows "completed" with PR URL
2. Navigate away mid-session, come back -> workspace fully restored
3. Approve a simple Slack reply card from feed -> message sent in Slack
4. Native notification fires for CRITICAL card

---

## Milestone 7: Dashboard, Chat, Briefing, Learning (Week 15-16)

**Goal:** Complete the v0.1 feature set with analytics, chat, daily briefing, and feedback-based learning.

### Deliverables

- [ ] **Dashboard UI (Svelte):**
  - Stat cards: events processed, cards generated, approved/edited/dismissed, pending
  - Estimated time saved (running total with configurable estimates per action type)
  - LLM cost tracking (from audit_log token counts + per-model pricing)
  - Events by source chart (Layerchart/Chart.js)
  - Approval rate by persona chart
  - Average response time
- [ ] **Dashboard API:** `GET /dashboard` endpoint with aggregation queries on SQLite
- [ ] **Chat sidebar UI (Svelte):**
  - Chat panel with message history
  - Chat input with send button
  - Messages display with Laya responses and referenced cards/events
- [ ] **Chat graph (LangGraph):**
  - Parse intent node (fast LLM)
  - Retrieve context node (ChromaDB + SQLite)
  - Respond node (strong LLM)
  - Reference specific cards and events in responses
- [ ] **Chat API:** WebSocket `chat_message` type + `POST /chat` REST fallback
- [ ] **Daily Briefing:**
  - Scheduler job (configurable time via settings.json)
  - Briefing worker: query overnight events, pending cards, today's calendar
  - Briefing stager: synthesize into briefing card with meeting prep context
  - Native notification: "Your morning briefing is ready"
- [ ] **Learning loop (prompt injection):**
  - feedback_query tool returns recent approval/edit/dismiss patterns
  - Router prompt includes: "For similar events, user edited priority from HIGH to LOW 3/5 times"
  - Measure: does approval rate improve over first 2 weeks of use?
- [ ] **Audit log viewer** in settings page (filterable table)

### Test Criteria

1. Dashboard shows accurate numbers matching SQLite data
2. Chat correctly answers "What happened with BUG-1234?" by finding related events and cards
3. Daily briefing generates at configured time with overnight summary + calendar context
4. After dismissing 5 low-priority Slack events, Router starts classifying similar events as lower priority

---

## Milestone 8: Polish, Packaging, Testing (Week 17-19)

**Goal:** Ship a production-quality v0.1 across all three platforms.

### Deliverables

- [ ] **First-run setup wizard (Svelte):**
  - Step 1: LLM configuration (model selection, API key entry -> keychain)
  - Step 2: Connect tools (link to n8n OAuth pages per service)
  - Step 3: Coding agent selection + repo directory picker
  - Step 4: Team member entry (name, email, role)
  - Step 5: Event filter presets (ignore bots, ignore status changes, etc.)
- [ ] **PyInstaller bundle** of Python engine (self-contained binary)
- [ ] **Tauri sidecar integration:** Launch bundled engine binary on app start
- [ ] **Docker management from Tauri:** Start/stop/restart n8n container, detect Docker availability
- [ ] **Platform-specific installers:**
  - macOS: DMG with drag-to-Applications, code signed
  - Linux: AppImage + .deb package
  - Windows: MSI installer
- [ ] **Tauri auto-update** configuration (stable/beta channels, update check URL)
- [ ] **System tray:** Health status indicator (green/yellow/red), badge count, quick actions
- [ ] **OS keychain integration** verified on all three platforms
- [ ] **Error handling throughout:**
  - n8n offline: show indicator, queue events, retry on reconnect
  - LLM API timeout: retry with backoff, show "processing delayed" on card
  - Coding agent crash: mark session as failed, preserve workspace state, show error
  - Invalid event: log validation error, drop event, don't crash
- [ ] **Graceful shutdown:** Save workspace states, stop agent sessions, stop n8n container
- [ ] **Database migrations** verified: fresh install and upgrade from earlier milestone builds
- [ ] **Test suites:**
  - Python unit tests (pytest): all graph nodes, tools, rules engine, models
  - Python integration tests: full event flow with mocked LLM, workspace persistence
  - Svelte component tests (Vitest): all major components
  - E2E tests (Playwright): setup wizard, event-to-card flow, workspace interaction, chat
- [ ] **Documentation:** Setup guide for users, developer setup guide
- [ ] **n8n workflow import script** verified on all platforms
- [ ] **Export Diagnostics** button: bundle logs + config (redacted) + system info into zip

### Test Criteria

1. Clean install on fresh macOS, Linux, and Windows machines
2. Setup wizard completes end-to-end
3. Events flow from all 5 sources
4. Cards generate with intelligence and staged outputs
5. Workspaces work with interactive agent sessions
6. Actions execute via n8n
7. Dashboard, chat, and briefing all functional
8. No crashes during 24-hour soak test
9. Update from a previous milestone build succeeds with database migration

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
