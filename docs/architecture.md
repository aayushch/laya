# Laya: System Architecture

## 1. Overview

Laya is a **local-first AI command centre** for professionals. It intercepts events from a user's professional tools (Jira, Bitbucket, Slack, Gmail, Calendar, GitHub, Linear, Outlook, Notion), performs autonomous research and action-staging using LLM-powered agents, and presents the user with ready-to-approve **Action Cards** in a desktop application.

**Core Principle:** By the time a human opens a notification, Laya has already researched context, drafted the response/fix/email, and packaged it into an Action Card for one-click approval.

### Key Characteristics

- **Local-first:** Runs entirely on the user's machine. No cloud infrastructure required.
- **Single-user:** Designed as a personal productivity tool, not a team platform.
- **Privacy-aware:** Sensitive data can be processed locally via Ollama. Three-tier data classification.
- **Event-driven:** n8n handles all external integrations. Laya Engine is a pure reasoning layer.
- **Human-in-the-loop:** All write operations require explicit user approval.

## 2. High-Level Architecture

```
External Services (Jira, Bitbucket, Slack, Gmail, Calendar)
         |
         | webhooks / polling (managed by n8n)
         v
+-----------------+    HTTP POST    +--------------------+
|      n8n        | --------------> |    Laya Engine     |
| (local Node.js) |   Laya Event    |    (Python)        |
|                 | <-------------- |                    |
|  Normalize      |   Action Req    |  Classify          |
|  events         |                 |  Research          |
|                 |                 |  Stage             |
|  Execute        |                 |  Learn             |
|  actions        |                 +---------+----------+
+-----------------+                           |
                                              | WebSocket (bidirectional)
                                              v
                                    +--------------------+
                                    |     Tauri UI       |
                                    |   (Svelte)         |
                                    |                    |
                                    |  Dashboard         |
                                    |  Action Card Feed  |
                                    |  Card Workspaces   |
                                    |  Chat Sidebar      |
                                    +--------------------+
```

### The Three Processes

| Process | Technology | Role |
|---|---|---|
| **Tauri App** | Rust + Svelte | Desktop shell, system tray, native notifications, manages other processes |
| **Laya Engine** | Python (FastAPI + asyncio) | Orchestration, LLM calls, memory, agent management |
| **n8n** | Local Node.js process (npm) | Sole gateway to external services (both inbound and outbound) |

## 3. Detailed Architecture

```
+--------------------------------------------------------------------------+
|                           USER'S MACHINE                                 |
|                                                                          |
|  +- TAURI v2 APP SHELL -----------------------------------------------+  |
|  |                                                                    |  |
|  |  +- Svelte Frontend --------------------------------------------+  |  |
|  |  |                                                              |  |  |
|  |  |  +- Dashboard -+    +- Feed ----------+  +- Chat ---------+  |  |  |
|  |  |  | Stats,      |    | Action Cards    |  | Conversational |  |  |  |
|  |  |  | Analytics,  |    | sorted by       |  | interface to   |  |  |  |
|  |  |  | Time saved, |    | priority/time   |  | query context  |  |  |  |
|  |  |  | LLM costs   |    | Bookmarks       |  |                |  |  |  |
|  |  |  +-------------+    | Simple: approve |  +----------------+  |  |  |
|  |  |                     |  from feed      |                      |  |  |
|  |  |  +- Workspace ----+ |                 |  +- Coherence ---+   |  |  |
|  |  |  | Timeline,      | | Complex: open   |  | Entity search |   |  |  |
|  |  |  | Live Agent,    | |  workspace      |  | Trace history |   |  |  |
|  |  |  | Context,       | |                 |  | AI narratives |   |  |  |
|  |  |  | Staged Outputs | +-----------------+  +---------------+   |  |  |
|  |  |                                                              |  |  |
|  |  |  +- Omni ----------+                                         |  |  |
|  |  |  | Rolling cross-  |                                         |  |  |
|  |  |  | platform        |                                         |  |  |
|  |  |  | summary, pins,  |                                         |  |  |
|  |  |  | time-travel     |                                         |  |  |
|  |  |  +-----------------+                                         |  |  |
|  |  |                                                              |  |  |
|  |  |  +- Settings -----+                                          |  |  |
|  |  |  | Models, keys,  |                                          |  |  |
|  |  |  | repos, team,   |                                          |  |  |
|  |  |  | rules, privacy |                                          |  |  |
|  |  |  +----------------+                                          |  |  |
|  |  |  +----------------+                                          |  |  |
|  |  +----------------------------+---------------------------------+  |  |
|  |                               | WebSocket (bidirectional)          |  |
|  |  Rust: System tray, native    |                                    |  |
|  |  notifications, sidecar mgmt  |                                    |  |
|  +-------------------------------+------------------------------------+  |
|                                  |                                       |
|  +- LAYA ENGINE (Python) --------+----------------------------------+    |
|  |                                                                  |    |
|  |  +- FastAPI Server -------------------------------------------+  |    |
|  |  |  POST /events          (receives from n8n)                 |  |    |
|  |  |  GET  /events/dead     (failed events for retry)           |  |    |
|  |  |  POST /actions/approve (receives from UI)                  |  |    |
|  |  |  GET  /cards           (UI fetches feed)                   |  |    |
|  |  |  POST /cards/run-agent (launch agent on card or entity)    |  |    |
|  |  |  GET  /dashboard       (UI fetches stats)                  |  |    |
|  |  |  POST /trace           (Coherence entity search)           |  |    |
|  |  |  POST /egress/execute  (outbound actions)                  |  |    |
|  |  |  CRUD /classification  (rules & corrections)               |  |    |
|  |  |  CRUD /cards/groups    (context merge/unlink)              |  |    |
|  |  |  GET  /budget          (LLM cost tracking)                 |  |    |
|  |  |  WS   /ws              (real-time bidirectional)           |  |    |
|  |  |  GET  /health          (health check)                      |  |    |
|  |  |  CRUD /settings        (configuration)                     |  |    |
|  |  |  GET  /omni            (rolling cross-platform summary)    |  |    |
|  |  +------------------------------------------------------------+  |    |
|  |                                                                  |    |
|  |  +- Asyncio Pipeline -----------------------------------------+  |    |
|  |  |                                                            |  |    |
|  |  | INGEST -> RULES -> ROUTER -> WORKER(s) -> STAGER -> EMIT   |  |    |
|  |  |                                                      |     |  |    |
|  |  |   LEARN CONTEXT <- TRACE <- GROUP SUMMARY <- CONTEXT ASSOC |  |    |
|  |  |     |                                                      |  |    |
|  |  |   CONTEXT LEARN -> OMNI                                    |  |    |
|  |  |                      |                                     |  |    |
|  |  |           +----------+-----------+                         |  |    |
|  |  |           |      LiteLLM*        |                         |  |    |
|  |  |           |  Router: Small model |                         |  |    |
|  |  |           |  Stager: Large model |                         |  |    |
|  |  |           |  Local: Ollama       |                         |  |    |
|  |  |           +----------------------+                         |  |    |
|  |  | *Note: LiteLLM can be configured with self-hosted models   |  |    |
|  |  |                                                            |  |    |
|  |  | Workers (6 Personas):                                      |  |    |
|  |  |   ENGINEER -> Coding Agent (Claude Code/Gemini/Codex PTY)  |  |    |
|  |  |   COMMS    -> LLM drafting with memory context             |  |    |
|  |  |   OPS      -> Calendar/event aggregation + LLM synthesis   |  |    |
|  |  |   FINANCE  -> Invoice/expense/budget processing            |  |    |
|  |  |   HR       -> People ops, onboarding, leave requests       |  |    |
|  |  |   SALES    -> Pipeline/deal/prospect tracking              |  |    |
|  |  +------------------------------------------------------------+  |    |
|  |                                                                  |    |
|  |  +- Internal Tools (Python functions) ------------------------+  |    |
|  |  |  memory_search | event_lookup | entity_lookup | entity_link|  |    |
|  |  |  team_lookup   | card_history | feedback_query             |  |    |
|  |  |  context_link  | context_confirm | budget_query            |  |    |
|  |  +------------------------------------------------------------+  |    |
|  |                                                                  |    |
|  |  +- Egress Module --------------------------------------------+  |    |
|  |  |  Outbound action execution for 9 platforms                 |  |    |
|  |  |  Gmail | Slack | Jira | GitHub | Bitbucket | Calendar      |  |    |
|  |  |  Linear | Outlook | Notion | Connection broker | OAuth mgmt|  |    |
|  |  +------------------------------------------------------------+  |    |
|  |                                                                  |    |
|  |  +- Scheduled Jobs -------------------------------------------+  |    |
|  |  |  Daily Briefing (cron) | Memory cleanup (weekly)           |  |    |
|  |  |  Classification learning (periodic rule extraction)        |  |    |
|  |  |  Context learning (every 6h, grouping rule extraction)     |  |    |
|  |  |  Omni resynthesis (daily, configurable time)               |  |    |
|  |  |  Dead event recovery (startup, stalled event detection)    |  |    |
|  |  +------------------------------------------------------------+  |    |
|  +------------------------------------------------------------------+    |
|          |                  |                    |                       |
|          | HTTP             | Read/Write         | Embed/Query           |
|          v                  v                    v                       |
|  +-----------------+   +---------------+   +-------------------+         |
|  |     n8n         |   |   SQLite      |   |    ChromaDB       |         |
|  | (local Node.js) |   |               |   |   (embedded)      |         |
|  |                 |   | events        |   |                   |         |
|  | Ingestion:      |   | action_cards  |   | laya_memory       |         |
|  |   Jira          |   | action_log    |   | collection        |         |
|  |   Bitbucket     |   | entities      |   |                   |         |
|  |   Slack         |   | workspace_*   |   | nomic-embed       |         |
|  |   Gmail         |   | traces        |   | (local)           |         |
|  |   Calendar      |   | egress_conn   |   |                   |         |
|  |   GitHub        |   | classif_*     |   |                   |         |
|  |   Linear        |   | context_*     |   |                   |         |
|  |                 |   | omni_*        |   |                   |         |
|  |                 |   | audit_log     |   |                   |         |
|  |                 |   +---------------+   +-------------------+         |
|  | Execution:      |                                                     |
|  |   Create PR     |   +---------------+    +------------------+         |
|  |   Send email    |   | Config        |    | Coding Agent     |         |
|  |   Post Slack    |   | Files         |    | (PTY subprocess) |         |
|  |   Add comment   |   |               |    |                  |         |
|  |   Update Jira   |   | settings.json |    | Claude Code  OR  |         |
|  |                 |   | team.json     |    | Gemini CLI   OR  |         |
|  +-----------------+   | repos.json    |    | Codex CLI        |         |
|                        | rules.json    |    |                  |         |
|                        +---------------+    +------------------+         |
|                                                                          |
|  +- Optional -------------------------------------------------------+    |
|  |  Ollama (local LLMs for privacy-sensitive processing)            |    |
|  +------------------------------------------------------------------+    |
|                                                                          |
+--------------------------------------------------------------------------+
```

## 4. n8n: The Integration Gateway

n8n is the **sole boundary** between Laya and external services. It has two responsibilities:

### Inbound (Ingestion)

- Listen to external services via webhooks or polling (n8n decides the mechanism per connector)
- Normalize raw platform payloads into the unified **Laya Event** schema (see [event-schema.md](./event-schema.md))
- POST normalized events to the Laya Engine at `http://localhost:8420/events`

### Outbound (Execution)

- Receive approved action requests from the Laya Engine via webhook
- Execute actions against external platforms using their APIs
- Return results to the Engine

### n8n Workflow Inventory

| Workflow | Type | Trigger | Action |
|---|---|---|---|
| jira-ingestion | Inbound | Jira webhook (ticket events) | Normalize + POST to engine |
| github-ingestion | Inbound | GitHub webhook (PR/issue events) | Normalize + POST to engine |
| bitbucket-ingestion | Inbound | Bitbucket webhook (PR/build events) | Normalize + POST to engine |
| slack-ingestion | Inbound | Slack events (messages, mentions) | Normalize + POST to engine |
| gmail-ingestion | Inbound | Gmail trigger (new emails) | Normalize + POST to engine |
| gmail-executor | Outbound | Engine webhook | Send/reply to email |
| google-calendar-ingestion | Inbound | Google Calendar trigger | Normalize + POST to engine |
| google-calendar-executor | Outbound | Engine webhook | Create/update calendar event |
| outlook-email-ingestion | Inbound | Outlook email trigger | Normalize + POST to engine |
| outlook-email-executor | Outbound | Engine webhook | Send/reply to email |
| outlook-imap-ingestion | Inbound | Outlook IMAP trigger | Normalize + POST to engine |
| outlook-imap-executor | Outbound | Engine webhook | Send/reply to email |
| outlook-calendar-ingestion | Inbound | Outlook Calendar trigger | Normalize + POST to engine |
| outlook-calendar-executor | Outbound | Engine webhook | Create/update calendar event |

### Key Design Properties

- **n8n holds all external service credentials.** Laya Engine never needs OAuth tokens for Jira, Slack, etc.
- **n8n is a "dumb pipe."** No classification, no context extraction, no AI logic. Pure structural normalization.
- **Workflows are pre-built and shipped with Laya** as JSON exports, auto-imported on first launch.

## 5. Laya Engine: The Orchestration Layer

The engine is a Python process running FastAPI (HTTP + WebSocket server) with an asyncio-based event processing pipeline.

### Event Processing Pipeline

```
n8n POST /events
       |
       v
+-------------+
|   INGEST    |  Pure code: parse event, resolve actor via team.json,
|             |  store raw event in SQLite
+------+------+
       |
       v
+-------------+
|   RULES     |  Pure code: evaluate user-defined rules (rules.json)
|   ENGINE    |  against event. Drop or pass.
+------+------+
       |
       v
+--------------+
|    ROUTER    |  LLM call (fast model via LiteLLM):
|              |  - Classify: category, persona, priority
| (Haiku /     |  - Extract entities (ticket IDs, file paths, people)
|  Flash /     |  - Generate research_plan
|  Self-hosted |  - Determine: requires_research? secondary_persona?
| )            |  - Inject learning feedback from past approvals
|              |  - Query ChromaDB for related past events
+------+-------+
       |
       +-- persona=ENGINEER, requires_research=true
       |         |
       |         v
       |   +-----------------------------------------------------------+
       |   | ENGINEER WORKER                                           |
       |   |                                                           |
       |   | 1. Gather internal context (memory, entities, past cards) |
       |   | 2. Build prompt with research_plan + context              |
       |   | 3. Spawn coding agent PTY session                         |
       |   | 4. Stream progress to UI via WebSocket                    |
       |   | 5. Surface agent approval requests to workspace UI        |
       |   | 6. Pipe user responses back to agent                      |
       |   | 7. Parse structured findings on completion                |
       |   +--------+--------------------------------------------------+
       |            |
       |            +-- secondary_persona=COMMS?
       |            |         |
       |            |         v
       |            |   +------------+
       |            |   |COMMS WORKER|  Draft reply using
       |            |   |            |  research findings as context
       |            |   +-----+------+
       |            |         |
       v            v         v
+--------------+
|    STAGER    |  LLM call (strong model via LiteLLM):
|              |  Synthesize research + drafts into polished Action Card
| (Sonnet /    |  Generate suggested_actions array
|  Opus /      |
|  Self-hosted |
| )            |
+------+-------+
       |
       v
+--------------+
|     EMIT     |  Pure code:
|              |  - Store Action Card in SQLite
|              |  - Embed card summary in ChromaDB
|              |  - Update entity cross-references
|              |  - Log to audit_log
|              |  - Push to UI via WebSocket
+------+-------+
       |
       v
+--------------+
|  POST-EMIT   |  Async fan-out (triggered by emit.py):
|              |  - entity_resolution: cross-platform entity linking
|              |  - context_grouping: semantic card grouping
|              |  - trace: ChromaDB indexing for semantic search
|              |  - tags: persist stager-suggested tags
|              |  - group_summary: rolling LLM summary per entity group
|              |  - omni: cross-platform rolling summary
|              |  - processing_rules: evaluate automated event→action rules
+------+-------+
       |
       v
Tauri UI shows Action Card
       |
       | (user clicks Approve)
       v
Engine POSTs action to n8n --> n8n executes --> result stored
```

### Chat Pipeline

```
User types question
       |
       v
PARSE INTENT (fast LLM)
  - Identify: status_query, context_lookup, card_reference, etc.
  - Extract: entities, time ranges
       |
       v
RETRIEVE CONTEXT (hybrid: vector + lexical, merged via RRF)
  - memory_search (ChromaDB) for semantic matches
  - FTS5/BM25 keyword search over cards_fts / events_fts
  - event_lookup (SQLite) for structured data
  - card_history (SQLite) for related Action Cards
  - entity_lookup (SQLite) for cross-platform refs
       |
       v
RESPOND (strong LLM)
  - Synthesize context into natural language answer
  - Reference specific cards and events
       |
       v
WebSocket --> UI chat panel
```

### Coherence Pipeline (Entity Search)

```
User searches for entity (e.g., "BUG-1234" or "Sarah")
       |
       v
DISCOVERY (parallel)
  - Semantic search (ChromaDB)
  - Fuzzy/SQLite search
  - Entity table lookup
  - Merge via Reciprocal Rank Fusion (RRF)
       |
       v
EXPANSION
  - Fetch all cards for matched entities
  - Resolve cross-references
       |
       v
CLUSTERING
  - Group by connected entities
  - Order chronologically
  - Auto-detect narrative chapters
       |
       v
NARRATIVE (LLM)
  - Generate AI summary of each cluster
  - Stream via WebSocket
       |
       v
Store trace in SQLite for reuse
```

### Egress System (Outbound Actions)

The egress module owns all outbound communication with external platforms. It provides a unified API for executing actions without the engine needing to know platform-specific details.

```
Action trigger (card approval, compose modal, chat)
       |
       v
EGRESS ROUTER
  - Resolve platform & action type
  - Validate payload against platform schema
       |
       v
PREVIEW (optional)
  - Generate preview for user confirmation
       |
       v
EXECUTE
  - Route to platform backend (n8n webhook or SMTP)
  - Track result in action_log
       |
       v
CONNECTION BROKER
  - Manage OAuth credentials
  - Health check connections
  - 9 platforms: Gmail, Slack, Jira, GitHub,
    Bitbucket, Calendar, Linear, Outlook, Notion
```

Each platform owns its own contract — capabilities, draft/compose schema, terminal event types, and payload `normalize`/`validate` logic — as a `Platform` subclass under `egress/platforms/` extending `platforms/base.py`. `registry.py` is a thin facade that delegates to these adapters, so adding a platform means writing one file rather than editing several parallel tables. SMTP is a data-only adapter (`platforms/smtp.py`) used by the SMTP execution backend for direct email delivery, and is excluded from compose enrichment.

### Omni Pipeline (Rolling Summary)

```
Card emitted (EMIT step)
       |
       v
TRIGGER OMNI UPDATE (debounced, 10s window)
  - Append structured item to Recent layer
  - No LLM call (cheap incremental update)
       |
       v
Scheduled daily resynthesis (configurable time)
       |
       v
RESYNTHESIS (LLM call)
  - Load latest snapshot + pinned items
  - Collect cards since last resynthesis
  - Separate user-acted cards (higher weight)
  - LLM compresses all four temporal layers:
    Attention | Recent | Period | Milestone
  - Enforce density constraints (N items, M words)
  - Create new versioned snapshot in SQLite
  - Broadcast omni_updated via WebSocket
       |
       v
UI displays updated Omni view
  - Version slider for time-travel
  - Pin/unpin items
  - Drill-down to source cards (Insight view)
```

### Classification Learning

Laya learns from user corrections to improve future card classification:

1. **Correction logging:** When a user changes a card's priority or persona, the correction is stored in `classification_corrections`
2. **Pattern extraction:** After 15+ unprocessed corrections accumulate, the learning pipeline calls the LLM to extract generalizable rules
3. **Rule injection:** Both manual and learned rules are injected into the router prompt for future classifications
4. **Continuous improvement:** Rules are per-space and improve over time as more corrections are processed

### Processing Rules

User-authored automation that runs against each emitted card:

- **Conditions** are a JSON DSL evaluated against the trigger context (event fields, classification, actor relationship). Regex patterns are validated at create-time.
- **Actions** are typed payloads — archive, dismiss, change priority/persona, send to platform, add tag, etc.
- **Throttling:** `rate_limit`, `cooldown_secs`, `max_daily` per rule. Repeated errors auto-disable the rule (threshold in settings).
- **Scoping:** `space_id = NULL` means a global rule that applies to all spaces; non-null targets a specific space.
- **AI conditions/actions:** A rule may use LLM-evaluated conditions or actions (e.g. classify-then-act, run an agent) gated by a confidence threshold, with a `dry_run` mode (see [ai-processing-rules.md](../engine/docs/ai-processing-rules.md)).
- **Firing log:** Every firing is recorded in `processing_rule_firings` with the actions tried, per-action results, and any error. The log is queryable and searchable by outcome (`success` / `error` / `skipped`) and backs the Settings → Rules activity view, surfacing rule effectiveness and failures.

### Tags & Daily Summaries

- **Tags** are global, polymorphic labels (`tags` + `tag_assignments`) that can be attached to a card, entity, or context group. The stager LLM suggests tags during card generation; processing rules and the UI can also assign them. Tag metadata is mirrored into ChromaDB for filtered semantic search. System tags (`spam`, `phishing`, `automated`) are seeded and immutable.
- **Daily summaries** (`daily_summaries`, keyed by `(date, space_id)`) back the Summary View — one structured-JSON summary row per space per day, regenerated by the briefing pipeline.
- **Metadata** (`metadata`, keyed by `(key, space_id)`) is a generic key/value store for opaque JSON consumed by n8n workflows, UI preferences, and similar consumers.

### Context Association Pipeline

When a new card is emitted, the EMIT step attempts to associate it with existing cards:

```
Card emitted (EMIT step)
       |
       v
SEMANTIC SEARCH (ChromaDB)
  - Query for similar card embeddings
  - Filter by space_id
       |
       v
CONFIDENCE CHECK
  - Distance < 0.20 → auto-confirm link
  - Distance 0.20-0.30 → LLM confirmation call
  - Distance > 0.30 → no link
       |
       v
CONTEXT GROUP
  - Create or join context_group (shared context_id)
  - Store members with confidence scores
  - Respect user-split decisions (never re-merge)
       |
       v
CONTEXT LEARNING (periodic, every 6 hours)
  - Batch unprocessed link/unlink corrections (up to 40)
  - LLM extracts generalizable grouping rules
  - Rules stored in context_rules table
  - Injected into future grouping prompts
```

### Context Rules

**Context rules** are natural-language grouping directives, distinct from the priority/persona *classification rules* above. They come in two flavours: `learned` (extracted from the user's link/unlink corrections by a cheap model, as in the pipeline above) and `manual` (authored by the user). Active rules of both kinds are injected into the context-association grouping prompt. When the learned set grows past a threshold, an LLM **consolidator** (`llm/prompts/context_rule_consolidator.py`) merges redundant learned rules while preserving manual rules verbatim, keeping the injected prompt compact. Rules are managed via `api/context_rules_api.py` and the Settings → Context Rules panel (list / create / edit / disable / delete).

### Run Agent Flow

Any card or entity group can have a coding agent run against it:

```
User presses Ctrl+A or clicks "Run Agent"
       |
       v
AGENT DIALOG
  - Custom prompt (what to investigate/code/research)
  - Agent selection (Claude Code / Gemini CLI / Codex / Pi CLI)
  - Working directory (repo path or ~/.laya/tmp/research/<card_id>/)
  - Additional directories
       |
       v
POST /cards/run-agent  OR  POST /entity/{id}/run-agent
  - Creates event + card with status=agent_running
  - For entity-level: writes CONTEXT.md with group summary + card details
  - session_type: "research" (sandboxed) or "code" (repo-based)
       |
       v
SPAWN AGENT (background task)
  - PTY subprocess with configured agent CLI
  - Streams events to workspace_events table + WebSocket
       |
       v
INTERACTIVE SESSION
  - Agent may ask questions → status=awaiting_input
  - User answers via POST /workspace/{session_id}/answer
  - User can dismiss questions or resume with new prompt
  - User can browse research output files
       |
       v
SESSION COMPLETE
  - Findings stored in session (findings_json)
  - Card transitions to ready status
  - Session can be resumed with POST /workspace/{session_id}/resume
```

### Agent Inference Backends

Beyond the interactive *workspace* role above, an installed CLI agent can also be driven as a **non-interactive LLM inference backend** for any pipeline stage (router, stager, omni, chat, …). A model id of the form `agent/<agent_id>/<model>` (agent_id ∈ `claude_code`, `codex_cli`, `gemini_cli`, `pi_cli`) routes the call through `llm/agent_backend.py` so the stage runs on the agent's own subscription quota instead of an API key or local VRAM. Two capability tiers exist: **native** (Claude Code, which enforces JSON schemas via `--json-schema` and returns pre-parsed output) and **best-effort** (the rest, which receive the schema as text and are parsed/validated/retried up to three times). Agent processes run headless in an ephemeral empty working directory with built-in tools denied, so they never touch the filesystem during inference.

### Agent Usage-Limit Budgeting

Agent backends bill against time-windowed usage limits (e.g. Claude Code's ~5-hour rolling window) rather than a dollar amount, so they have a budgeting path separate from the monthly `$` cost cap. `pipeline/agent_budget.py` tracks usage per agent and, when an agent crosses its configured pause threshold, auto-pauses the affected ingestion workflows (recorded in `agent_budget_paused_workflows`) and auto-resumes them at the window reset via the scheduler. Native rate-limit signals — Claude Code's `rate_limit_event` — are scraped into `agent_rate_limit_state` and surfaced in the Settings model panel and the activity-footer budget widget. Singleton pause state lives in `agent_budget_state` so it survives restarts.

### Filtered Events & Audit Export

Events dropped by a filter rule are **terminal and informational** — unlike dead events, they are never retried. They are listed via `GET /events/filtered` (with the rule that dropped each one) and can be exported as JSON over a time window via `GET /events/filtered/export?days=`. The audit log is likewise exportable (`GET /audit-log/export`, respecting the current step/success/search filters). The UI rolls dead events, ingestion errors, and filtered events into a single Audit view with a red-dot indicator (seeded by `GET /audit/failure-summary`, updated live over WebSocket) and retry/clear controls.

### On-Prem Repositories

Each repo in `repos.json` carries an optional `host` field that distinguishes cloud from self-hosted. An empty host, or a cloud domain (`bitbucket.org`, `github.com`), means Cloud; any other value (e.g. `git.acme.com`) marks a self-hosted instance — Bitbucket Server / Data Center or GitHub Enterprise — and the egress platform adapts its API base URL and auth scheme accordingly. A missing host always resolves to Cloud, so existing configs keep working.

### Dead Event Recovery

Events that fail processing 3 times are marked as "dead":

- `GET /events/dead` lists permanently failed events with error context
- `POST /events/dead/retry` re-enqueues specific or all dead events
- Each manual retry increments a `manual_retries` counter and resets processing state for 3 fresh attempts

### Pipeline State

The pipeline passes state through each step using a database-backed event queue with concurrency control (configurable semaphore, default 5 concurrent events). Events have a `processing_status` (queued, processing, completed, failed) with exponential backoff retry and stalled event recovery on startup.

## 6. Card Workspace Model

Action Cards are not just notifications -- they are **stateful workspaces** for complex, multi-step tasks.

### Card Complexity Levels

| Level | Example | Interaction |
|---|---|---|
| **Simple** | Slack reply, calendar prep | One-click approve/dismiss from feed |
| **Complex** | Bug fix, code review, PR staging | Open workspace with interactive agent session |

### Workspace Components

- **Timeline:** Chronological log of all events within this card (classification, research steps, agent messages, user decisions)
- **Live Agent Panel:** Real-time view of the coding agent's progress. Surfaces approval requests. Accepts user input.
- **Context Sidebar:** Related entities, cross-platform references, past events, team info.
- **Staged Outputs:** Final artifacts (code diffs, drafted emails, PR descriptions) ready for approval.

### Workspace State Persistence

All workspace interactions are stored in SQLite (`workspace_sessions` + `workspace_events` tables). When the user navigates away from a workspace and returns, the full state is restored. Agent subprocess state is managed independently -- agents continue running in the background.

### Interactive Agent Sessions

Coding agents (Claude Code, Gemini CLI, Codex) run as PTY subprocesses. The ENGINEER Worker:
1. Spawns the agent in the target repo directory
2. Streams agent stdout to the workspace timeline via WebSocket
3. Intercepts agent approval requests (e.g., "Modify 3 files?")
4. Surfaces them as interactive prompts in the workspace UI
5. Pipes user responses back to agent stdin
6. Parses structured output on agent completion

## 7. Memory & Context Layer

### Dual Storage

| Store | SQLite | ChromaDB |
|---|---|---|
| **Purpose** | Structured data, exact lookups | Semantic search, similarity matching |
| **Stores** | Events, cards, actions, entities, workspaces, audit log | Embeddings of event content, research findings, card summaries, chat history |
| **Queried by** | INGEST, EMIT, UI, audit | ROUTER, Workers, Chat |

### Hybrid Retrieval (Vector + FTS5/BM25)

Card and event retrieval (chat tools, Coherence) is **hybrid**: it combines dense vector search over ChromaDB with lexical BM25 search over SQLite FTS5 virtual tables, and merges the two ranked lists via Reciprocal Rank Fusion. The FTS layer (`db/fts.py`) maintains `cards_fts` (header, summary, intelligence, thread_context) and `events_fts` (subject_title, content_body); both are rebuilt at startup and kept in sync by SQL triggers on the base tables. If the SQLite build lacks the FTS5 module, retrieval degrades gracefully to `LIKE` rather than hard-failing.

**Contextual BM25:** the EMIT step persists a short `action_cards.thread_context` blurb on each card — the rolling group-summary headline + status when available, otherwise the headers of the last couple of cards in the thread. Because `thread_context` is indexed by FTS5, terse follow-up cards ("Approved.", "Resolved as Fixed.") remain findable by their thread's keywords even though their own text is sparse. The blurb is forward-only (NULL for first cards and pre-existing rows), matching the snapshot-at-emit philosophy of the embedding.

### Entity Resolution (Three Layers)

1. **Explicit links (deterministic):** Extract cross-references from platform data (Jira ticket IDs in PR descriptions, PR links in commits)
2. **Semantic matching (embedding-based):** ChromaDB similarity search to find potentially related events across platforms
3. **LLM confirmation (intelligent):** Ask the LLM to confirm whether semantically similar events refer to the same entity before creating a link

### Memory Lifecycle

| Content | Retention |
|---|---|
| Raw events | 90 days active |
| Action Cards | Indefinite |
| ChromaDB embeddings | 90 days active |
| Entity records | Indefinite |
| Audit logs | Indefinite |

### Embedding Model

Default: ChromaDB's built-in ONNX embedding function (~15MB, no GPU required). Optionally uses `nomic-embed-text-v1.5` via `sentence-transformers` if `requirements-ml.txt` dependencies are installed. Embedding model selection is hardwired (not user-configurable) to ensure consistent vector space across the application.

## 8. Security Architecture

### Credential Management

| Credential Type | Storage |
|---|---|
| LLM API keys | OS keychain (macOS Keychain, Linux libsecret, Windows Credential Manager) |
| Platform OAuth tokens | n8n's encrypted credential store |
| Local config (repos, team) | JSON files in ~/.laya/ |

### Three-Tier Data Classification

| Tier | Content | Processing |
|---|---|---|
| **Tier 1** | Metadata, timestamps, IDs, project names | Always cloud |
| **Tier 2** | Jira descriptions, PR diffs, Slack channel messages | Default cloud |
| **Tier 3** | Slack DMs, email bodies, flagged content | Cloud with privacy warning on card |

### Prompt Injection Defense

- Untrusted event content structurally delimited in prompts (`[EVENT CONTENT - UNTRUSTED INPUT]`)
- No autonomous writes -- all writes go through Action Card approval
- Output validated against JSON schema

### Audit Trail

Every processing step logged to `audit_log` table with: model used, processing tier, token counts, latency, errors.

## 9. Tech Stack Summary

```
FRONTEND (Tauri v2)
  - Svelte 5
  - Skeleton UI
  - Tailwind CSS
  - Layerchart / Chart.js (analytics)
  - Svelte stores (state management)

BACKEND (Python 3.10+)
  - FastAPI (HTTP + WebSocket server)
  - asyncio pipeline (event processing)
  - LiteLLM (unified LLM interface)
  - ChromaDB (embedded vector store, ONNX embeddings)
  - sentence-transformers (optional, for nomic-embed)
  - aiosqlite (async SQLite with WAL mode)
  - asyncio + pty (agent subprocess management)
  - structlog (structured JSON logging)

INFRASTRUCTURE
  - Tauri v2 / Rust (app shell, sidecar management)
  - n8n / local npm (integration gateway)
  - Ollama (optional, local LLMs)

DATA
  - SQLite (~/.laya/data/laya.db)
  - ChromaDB (~/.laya/data/chroma/)
  - JSON configs (~/.laya/*.json)

CODING AGENTS (user configures one or more)
  - Claude Code CLI
  - Gemini CLI
  - OpenAI Codex CLI
  - Pi CLI
  (usable both as workspace agents and as LLM inference backends)

TESTING
  - pytest + pytest-asyncio (engine)
  - Vitest + Svelte Testing Library (frontend)
  - Playwright (E2E)
```

## 10. Related Documents

- [Event Schema Specification](./event-schema.md)
- [API Contracts](./api-contracts.md)
- [Project Structure](./project-structure.md)
- [Implementation Plan](./implementation-plan.md)
- [Decision Log](./decision-log.md)
