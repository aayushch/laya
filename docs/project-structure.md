# Laya Project Structure

## Repository Layout

```
laya/
|-- README.md
|
|-- engine/                               # Python backend
|   |-- requirements.txt                  # Core dependencies (FastAPI, LiteLLM, ChromaDB, etc.)
|   |-- requirements-ml.txt               # Optional ML dependencies (torch, sentence-transformers)
|   |-- laya-engine.spec                  # PyInstaller spec (optional, for standalone binary builds)
|   |-- laya/
|   |   |-- __init__.py
|   |   |-- main.py                       # FastAPI app + startup lifecycle
|   |   |-- config.py                     # Settings loader, agent path detection, directory management
|   |   |-- logging_setup.py              # Structured logging (structlog, rotating file handler)
|   |   |-- scheduler.py                  # Background scheduler (briefings, housekeeping)
|   |   |-- http_client.py                # Shared HTTP client
|   |   |
|   |   |-- api/                          # HTTP + WebSocket endpoints (27 routers)
|   |   |   |-- __init__.py
|   |   |   |-- events.py                 # POST /events (receives from n8n)
|   |   |   |-- cards_api.py              # Card CRUD, grouping, archive/reopen, bookmarks
|   |   |   |-- actions_api.py            # POST /actions/approve, /actions/dismiss
|   |   |   |-- workspace_api.py          # Workspace/agent session endpoints
|   |   |   |-- chat_api.py               # Chat conversations
|   |   |   |-- dashboard_api.py          # GET /dashboard (analytics)
|   |   |   |-- settings_api.py           # CRUD /settings
|   |   |   |-- spaces_api.py             # Space/source CRUD, bulk assignment
|   |   |   |-- connections_api.py         # Integration connection status
|   |   |   |-- rules_api.py              # Event filter rules
|   |   |   |-- classification_api.py     # Classification rules CRUD, corrections listing
|   |   |   |-- context_rules_api.py      # Context (grouping) rules CRUD -- learned + manual
|   |   |   |-- processing_rules_api.py   # Processing-rules CRUD, preview, firing log, settings
|   |   |   |-- tags_api.py               # Tag CRUD, assign/unassign, tags-for-target
|   |   |   |-- metadata_api.py           # Generic key/value metadata store (per-space)
|   |   |   |-- trace_api.py              # Coherence entity search, traces, narratives
|   |   |   |-- egress_api.py             # Outbound action execution, preview, connections
|   |   |   |-- omni_api.py              # Omni rolling summary: snapshots, pins, resynthesis
|   |   |   |-- budget_api.py             # $ budget + agent usage-limit budget, cost controls
|   |   |   |-- audit_api.py              # Audit log queries
|   |   |   |-- diagnostics_api.py        # System diagnostics
|   |   |   |-- ingestion_errors.py       # Failed-ingestion event listing/clear
|   |   |   |-- health.py                 # GET /health
|   |   |   |-- team.py                   # Team member endpoints
|   |   |   |-- websocket.py              # WS /ws handler
|   |   |   |-- ws_router.py              # WebSocket message routing
|   |   |
|   |   |-- pipeline/                     # Event processing pipeline (asyncio-based)
|   |   |   |-- __init__.py
|   |   |   |-- queue.py                  # Database-backed event queue with concurrency control
|   |   |   |-- ingest.py                 # INGEST step: parse event, resolve actor, store in SQLite
|   |   |   |-- rules.py                  # RULES step: evaluate user-defined rules (rules.json)
|   |   |   |-- router.py                 # ROUTER step: LLM classification (category, persona, priority)
|   |   |   |-- stager.py                 # STAGER step: LLM card generation
|   |   |   |-- emit.py                   # EMIT step: store card (+ thread_context), embed, context assoc, push via WebSocket
|   |   |   |-- executor.py               # Execute approved actions via n8n
|   |   |   |-- trace.py                  # Coherence: entity search, clustering, narrative generation
|   |   |   |-- learn.py                  # Classification learning: extract rules from corrections
|   |   |   |-- context_grouping.py       # Context association: semantic grouping across entity boundaries
|   |   |   |-- context_learn.py          # Context learning: extract grouping rules from user corrections
|   |   |   |-- entity_resolution.py      # Cross-platform entity linking with context awareness
|   |   |   |-- group_summary.py           # Rolling LLM summaries for multi-card entity groups
|   |   |   |-- processing_rules.py       # Processing rules evaluation
|   |   |   |-- tags.py                   # Persist stager-suggested tags + ChromaDB tag metadata
|   |   |   |-- omni.py                   # Omni: rolling summary, incremental updates, resynthesis
|   |   |   |-- budget.py                 # LLM cost tracking by feature and pipeline step
|   |   |   |-- agent_budget.py           # Agent inference-backend usage limits (window-based pause/resume)
|   |   |   |-- chat.py                   # Chat assistant pipeline
|   |   |   |-- workers.py                # Multi-persona LLM workers (6 personas)
|   |   |   |-- space_resolution.py       # Space/source identification
|   |   |   |-- feedback.py               # Learning loop (approval pattern tracking)
|   |   |   |-- summarize.py              # Daily briefing generation
|   |   |   |-- briefing.py               # Briefing content assembly
|   |   |
|   |   |-- workers/                      # Persona workers (6 personas)
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # BaseWorker interface
|   |   |   |-- engineer.py               # ENGINEER worker (coding agent orchestration)
|   |   |   |-- comms.py                  # COMMS worker (draft replies)
|   |   |   |-- ops.py                    # OPS worker (calendar prep, briefings)
|   |   |   |-- finance.py                # FINANCE worker (invoices, expenses, budgets)
|   |   |   |-- hr.py                     # HR worker (people ops, onboarding, leave)
|   |   |   |-- sales.py                  # SALES worker (pipeline, deals, prospects)
|   |   |
|   |   |-- agents/                       # Coding agent adapters
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # CodingAgent protocol + AgentSession protocol
|   |   |   |-- claude_code.py            # Claude Code CLI adapter
|   |   |   |-- gemini_cli.py             # Gemini CLI adapter
|   |   |   |-- codex_cli.py              # OpenAI Codex CLI adapter
|   |   |   |-- pi_cli.py                 # Pi CLI adapter
|   |   |   |-- session_manager.py        # Manages active sessions across cards
|   |   |
|   |   |-- llm/                          # LLM interaction layer
|   |   |   |-- __init__.py
|   |   |   |-- client.py                 # LiteLLM wrapper with model selection, retries, space overrides
|   |   |   |-- agent_backend.py          # Drive CLI agents (Claude Code/Codex/Gemini/Pi) as inference backends
|   |   |   |-- providers.py              # Custom model provider management
|   |   |   |-- prompts/                  # Prompt templates (router, stager, engineer, comms, ops, finance, hr, sales, omni, research, group_summary, context_learner, context_rule_consolidator, etc.)
|   |   |   |-- tools/                    # MCP tool definitions
|   |   |       |-- definitions.py        # Tool schemas
|   |   |       |-- card_tools.py         # Card query tools
|   |   |       |-- contact_tools.py      # Contact search/lookup tools
|   |   |       |-- constants.py          # Search and pipeline limit constants
|   |   |       |-- entity_tools.py       # Entity lookup/link tools
|   |   |       |-- event_tools.py        # Event query tools
|   |   |       |-- search_tools.py       # Memory/semantic search tools
|   |   |       |-- settings_tools.py     # Settings management tools
|   |   |       |-- rules_tools.py        # Chat-driven CRUD for filter/classification/processing rules
|   |   |       |-- executor.py           # Tool execution
|   |   |
|   |   |-- mcp/                          # Model Context Protocol server
|   |   |   |-- __init__.py
|   |   |   |-- __main__.py
|   |   |   |-- server.py                 # MCP server implementation
|   |   |
|   |   |-- db/                           # Database layer
|   |   |   |-- __init__.py
|   |   |   |-- sqlite.py                 # Async SQLite connection (WAL mode, foreign keys)
|   |   |   |-- chromadb_store.py         # ChromaDB vector store (embedded PersistentClient)
|   |   |   |-- fts.py                    # SQLite FTS5 virtual tables (cards_fts, events_fts) + BM25 query builder
|   |   |   |-- chunking.py              # Document chunking for embeddings
|   |   |   |-- migrate.py               # Migration runner (check version, apply pending)
|   |   |   |-- migrations/              # 70 numbered SQL migration files
|   |   |       |-- 001_initial.sql       # Core tables: events, action_cards, action_log
|   |   |       |-- 002_entities.sql      # Entity resolution: entities table
|   |   |       |-- ...
|   |   |       |-- 014_spaces.sql        # Spaces, sources, space_api_keys
|   |   |       |-- ...
|   |   |       |-- 026_chat_conversations.sql
|   |   |       |-- 028_classification_feedback.sql  # Corrections + rules tables
|   |   |       |-- 030_bookmarks.sql     # Card bookmarking
|   |   |       |-- 033_traces.sql        # Coherence entity search
|   |   |       |-- 034_egress_connections.sql  # Platform connections
|   |   |       |-- 035_traces_fuzzy.sql  # Fuzzy search optimization
|   |   |       |-- ...
|   |   |       |-- 044_manual_retries.sql    # Dead event manual retry counter
|   |   |       |-- 045_context_groups.sql    # Context groups, members tables
|   |   |       |-- 046_context_learning.sql  # Context corrections + rules tables
|   |   |       |-- ...
|   |   |       |-- 053_group_summaries.sql   # Rolling LLM group summaries
|   |   |       |-- 055_context_members_card_level.sql  # Card-level context members
|   |   |       |-- 057_entity_agent_sessions.sql  # Agent session per entity
|   |   |       |-- 058_ingestion_errors_cleared.sql  # Ingestion error tracking
|   |   |       |-- 059_processing_rules.sql  # Processing rules + firings
|   |   |       |-- 060_processing_rules_constraints.sql  # FK + JSON validity checks
|   |   |       |-- 061_normalize_space_id.sql  # NULL → 'default' for cards/events
|   |   |       |-- 062_repo_qualify_entity_ids.sql  # Repo-scoped GitHub/Bitbucket entities
|   |   |       |-- 063_read_at.sql       # read_at column on action_cards
|   |   |       |-- 064_daily_summaries_per_space.sql  # Daily summaries per (date, space_id)
|   |   |       |-- 065_tags.sql          # tags + tag_assignments (polymorphic labels)
|   |   |       |-- 066_metadata.sql      # Generic key/value metadata table
|   |   |       |-- 067_idx_events_created_at.sql   # Index for throughput / time-series charts
|   |   |       |-- 068_card_thread_context.sql     # action_cards.thread_context (Contextual BM25)
|   |   |       |-- 069_idx_proc_firings_fired.sql  # Index for firing-log queries
|   |   |       |-- 070_agent_budget.sql            # Agent usage-limit state, paused workflows, rate-limit
|   |   |
|   |   |-- models/                       # Pydantic data models
|   |   |   |-- __init__.py
|   |   |   |-- event.py                  # LayaEvent model
|   |   |   |-- card.py                   # ActionCard model
|   |   |   |-- workspace.py              # WorkspaceSession, WorkspaceEvent models
|   |   |   |-- trace.py                  # TraceEntity, TraceCluster, TraceChapter models
|   |   |   |-- omni.py                   # OmniItem, OmniSection, OmniSnapshot, OmniPin, OmniStats
|   |   |   |-- ...                       # Additional models (action, classification, etc.)
|   |   |
|   |   |-- egress/                       # Outbound action execution module
|   |   |   |-- __init__.py               # Public API: execute, preview, capabilities, connect
|   |   |   |-- models.py                 # EgressRequest, EgressResult, Connection models
|   |   |   |-- router.py                 # Route actions to correct platform backend
|   |   |   |-- registry.py               # Thin facade delegating to per-platform Platform classes
|   |   |   |-- connections.py            # Connection management and health checks
|   |   |   |-- tools.py                  # Egress tool definitions for chat
|   |   |   |-- tool_handlers.py          # Tool invocation handlers
|   |   |   |-- oauth.py                  # OAuth credential management
|   |   |   |-- health.py                 # Connection health monitoring
|   |   |   |-- backends/                 # Execution backends
|   |   |   |   |-- base.py               # Backend protocol
|   |   |   |   |-- n8n.py                # n8n webhook executor
|   |   |   |   |-- smtp.py               # Direct SMTP executor
|   |   |   |-- platforms/                # Per-platform Platform subclasses (capabilities, draft schema, validate)
|   |   |       |-- base.py               # Platform base class extended by each platform
|   |   |       |-- gmail.py              # Gmail: send, reply, forward
|   |   |       |-- slack.py              # Slack: post, reply in thread
|   |   |       |-- jira.py               # Jira: comment, update ticket
|   |   |       |-- github.py             # GitHub: create PR, comment
|   |   |       |-- bitbucket.py          # Bitbucket: create PR, comment
|   |   |       |-- calendar.py           # Calendar: create/update events
|   |   |       |-- linear.py             # Linear: create issue, comment
|   |   |       |-- notion.py             # Notion: create pages, update properties
|   |   |       |-- outlook.py            # Outlook: send, reply
|   |   |       |-- smtp.py               # SMTP: data-only adapter for the SMTP backend
|   |   |
|   |   |-- integrations/                 # External service clients
|   |   |   |-- __init__.py
|   |   |   |-- n8n_bootstrap.py          # n8n owner setup, API key creation, workflow import
|   |   |   |-- n8n_client.py             # n8n REST API client
|   |   |   |-- platforms.py              # Platform detection utilities
|   |   |
|   |   |-- security/                     # Security utilities
|   |       |-- __init__.py
|   |       |-- keychain.py               # OS keychain read/write (macOS/Linux/Windows)
|   |
|   |-- .venv/                            # Dev Python virtual environment (created by setup-dev.sh)
|
|-- ui/                                   # Tauri + Svelte frontend
|   |-- src-tauri/                        # Rust (Tauri shell)
|   |   |-- Cargo.toml
|   |   |-- tauri.conf.json               # Tauri config: windows, resources, icons
|   |   |-- build.rs                      # Rust build script (tauri-build)
|   |   |-- src/
|   |   |   |-- main.rs                   # Tauri app entry point
|   |   |   |-- lib.rs                    # Tauri setup, commands, health polling, tray menu
|   |   |   |-- sidecar.rs               # Python venv lifecycle & engine process management
|   |   |   |-- n8n.rs                    # n8n npm install + process lifecycle management
|   |   |-- icons/                        # App icons per platform (png, icns, ico)
|   |   |-- capabilities/                 # Tauri v2 capability definitions (shell, spawn perms)
|   |   |-- resources/                    # Bundled engine source (populated by build.sh)
|   |
|   |-- src/                              # Svelte 5 frontend (runes syntax)
|   |   |-- app.html                      # HTML entry point
|   |   |-- app.css                       # Tailwind v4 + theme system (dark/light, brand tokens)
|   |   |-- app.d.ts                      # TypeScript declarations
|   |   |
|   |   |-- lib/
|   |   |   |-- stores/                   # Svelte stores (reactive state)
|   |   |   |   |-- chat.ts               # Chat message history
|   |   |   |   |-- compose.ts            # Compose modal state (platform, action, prefill)
|   |   |   |   |-- feedFilters.ts        # Feed filter state (includes search-all-days mode)
|   |   |   |   |-- feedSelection.ts      # Selected card state (for bulk actions)
|   |   |   |   |-- feedView.ts           # Feed view mode (card/list)
|   |   |   |   |-- health.ts             # System health state
|   |   |   |   |-- recentCards.ts        # Recent card tracking
|   |   |   |   |-- setup.ts              # First-run setup state
|   |   |   |   |-- spaces.ts             # Space/source state
|   |   |   |   |-- trace.ts              # Coherence trace state
|   |   |   |   |-- theme.ts              # Theme (dark/light/glass), persists to localStorage
|   |   |   |   |-- websocket.ts          # WebSocket connection state
|   |   |   |
|   |   |   |-- api/                      # Engine communication layer
|   |   |   |   |-- engine.ts             # REST API client (all endpoints)
|   |   |   |   |-- types.ts              # TypeScript types matching API contracts
|   |   |   |
|   |   |   |-- components/              # Reusable UI components
|   |   |       |-- feed/                 # ActionCard, CardGroup, CardDetail, ListRow, ListGroup,
|   |   |       |                         #   LinkDialog, ClassificationDialog, DaySummary,
|   |   |       |                         #   GroupSummaryDetail, BulkActionsDropdown, StatusDot
|   |   |       |-- workspace/            # AgentPanel, TimelinePanel, ContextPanel
|   |   |       |-- trace/                # TraceCard, TraceHeader, TraceTimeline, TraceSearch, etc.
|   |   |       |-- omni/                # OmniView, OmniHeader, OmniItem
|   |   |       |-- egress/               # ComposeModal, InlineEditor, ConfirmAction, QuickActions
|   |   |       |-- dashboard/            # StatCard, FeatureCostChart, charts, analytics components
|   |   |       |-- chat/                 # ChatPanel, ChatMessage, ChatInput
|   |   |       |-- settings/             # ModelConfig, SpacesConfig, IntegrationsConfig,
|   |   |       |                         #   AppearanceConfig, KeybindingsConfig, AuditLogViewer,
|   |   |       |                         #   BriefingConfig, ProcessingRulesEditor, etc.
|   |   |       |-- setup/                # SetupWizard steps
|   |   |       |-- common/               # LoadingSpinner, EmptyState, ErrorDisplay, etc.
|   |   |
|   |   |-- routes/                       # SvelteKit pages
|   |       |-- +layout.svelte            # Main layout: sidebar nav + content area + chat
|   |       |-- +page.svelte              # Home page (redirects to /feed)
|   |       |-- feed/
|   |       |   |-- +page.svelte          # Action Card feed (with bookmarks filter)
|   |       |-- omni/
|   |       |   |-- +page.svelte          # Omni rolling summary (with space selector)
|   |       |   |-- insight/
|   |       |       |-- +page.svelte      # Omni drill-down: card panels + contextual chat
|   |       |-- coherence/
|   |       |   |-- +page.svelte          # Coherence: entity search & trace viewer
|   |       |-- dashboard/
|   |       |   |-- +page.svelte          # Analytics dashboard
|   |       |-- workspace/
|   |       |   |-- [card_id]/
|   |       |       |-- +page.svelte      # Card workspace view
|   |       |-- settings/
|   |       |   |-- +page.svelte          # Settings page (tabs)
|   |       |-- setup/
|   |       |   |-- +page.svelte          # First-run wizard
|   |       |-- status/
|   |       |   |-- +page.svelte          # System status page
|   |       |-- legal/
|   |           |-- +page.svelte          # Terms & license page
|   |
|   |-- package.json                      # Node dependencies (SvelteKit, Tauri CLI, Tailwind, etc.)
|   |-- svelte.config.js                  # SvelteKit config (static adapter, SPA fallback)
|   |-- vite.config.ts                    # Vite bundler config (Tailwind v4 plugin)
|   |-- tsconfig.json                     # TypeScript config (strict mode)
|
|-- n8n/                                  # Pre-built n8n workflow definitions (21 files)
|   |-- workflows/
|   |   |-- gmail-ingestion.json          # Gmail polling trigger
|   |   |-- gmail-executor.json           # Send, forward, archive, star, mark_read
|   |   |-- slack-ingestion.json          # Slack event webhook
|   |   |-- slack-executor.json           # Send, reply, react
|   |   |-- jira-ingestion.json           # Jira polling trigger
|   |   |-- jira-executor.json            # Comment, transition, create, assign
|   |   |-- github-ingestion.json         # GitHub webhook
|   |   |-- github-executor.json          # Comment, close, approve/merge/create PR
|   |   |-- bitbucket-ingestion.json      # Bitbucket webhook
|   |   |-- bitbucket-executor.json       # Comment, approve/decline/merge PR
|   |   |-- google-calendar-ingestion.json
|   |   |-- google-calendar-executor.json # Create/update/delete events
|   |   |-- linear-ingestion.json         # Linear polling
|   |   |-- linear-executor.json          # Create, comment, update, assign (GraphQL)
|   |   |-- notion-ingestion.json         # Notion page polling
|   |   |-- notion-executor.json          # Create/update pages
|   |   |-- outlook-email-ingestion.json
|   |   |-- outlook-email-executor.json   # Send, reply
|   |   |-- outlook-calendar-ingestion.json
|   |   |-- outlook-calendar-executor.json # Create/update/delete events
|   |   |-- laya-error-handler.json       # Default error workflow
|   |-- import.sh                         # Script to import workflows into n8n via REST API
|
|-- scripts/                              # Development and build scripts
|   |-- setup-dev.sh                      # One-time: creates venv, installs deps, installs n8n
|   |-- dev.sh                            # Start engine + Tauri dev server
|   |-- build.sh                          # Production build (bundle engine source + Tauri build)
|   |-- update_icons.sh                   # Icon generation from SVG
|
|-- landing/                              # Landing page (separate web assets)
|   |-- index.html
|
|-- docs/                                 # Architecture & design documentation
|   |-- architecture.md                   # System architecture with diagrams
|   |-- event-schema.md                   # Laya Event schema specification
|   |-- api-contracts.md                  # REST, WebSocket, and inter-service APIs
|   |-- database-schema.md                # SQLite tables and ChromaDB collection design
|   |-- project-structure.md              # This file
|   |-- implementation-plan.md            # Milestones and timeline
|   |-- decision-log.md                   # Architectural decisions with rationale
|   |-- n8n-data-persistence.md           # n8n data storage and backup strategies
```

## User Data Directory

Laya stores all user data in `~/.laya/`:

```
~/.laya/
|-- settings.json              # App settings: models, privacy, notifications, briefing
|-- team.json                  # Team members: name, email, role (manager/teammate/external/bot)
|-- repos.json                 # Local repos: name, path, platform, remote_id
|-- rules.json                 # Event filter rules: conditions + actions (drop/modify)
|-- data/
|   |-- laya.db                # SQLite database (events, cards, workspaces, spaces, audit)
|   |-- chroma/                # ChromaDB persistence directory (vector embeddings)
|-- logs/
|   |-- engine.log             # Application logs (rotating, 10 MB x 5 files); level = logging.level / LAYA_LOG_LEVEL
|   |-- engine-stdout.log      # Captured engine stdout/stderr (rotating, 10 MB x 3 files)
|   |-- n8n.log                # Captured n8n stdout/stderr (rotating, 10 MB x 3 files)
|-- venv/                      # Managed Python venv (production, created by Tauri on first launch)
|-- n8n_module/                # n8n npm installation
|-- n8n/                       # n8n runtime data (database, encryption key, credentials)
```

## Configuration File Schemas

### settings.json
```json
{
  "models": {
    "router": "claude-haiku-4-5-20251001",
    "stager": "claude-sonnet-4-5-20250929",
    "chat": "claude-sonnet-4-5-20250929",
    "local": "ollama/llama3"
  },
  "coding_agent": "claude_code",
  "privacy": {
    "tier3_sources": ["gmail", "slack_dm"],
    "tier3_processing": "cloud_with_warning"
  },
  "briefing": {
    "enabled": true,
    "time": "07:00",
    "timezone": "America/New_York"
  },
  "notifications": {
    "enabled": true,
    "min_priority": "HIGH"
  },
  "logging": {
    "level": "INFO"
  },
  "n8n": {
    "url": "http://localhost:45678",
    "webhooks": {
      "jira_executor": "/webhook/jira-exec-id",
      "slack_executor": "/webhook/slack-exec-id",
      "gmail_executor": "/webhook/gmail-exec-id",
      "calendar_executor": "/webhook/cal-exec-id"
    }
  }
}
```

### team.json
```json
{
  "members": [
    {
      "name": "Sarah Chen",
      "email": "sarah@company.com",
      "role": "teammate",
      "notes": "Backend engineer, payments team"
    },
    {
      "name": "Mike Torres",
      "email": "mike@company.com",
      "role": "manager",
      "notes": "Engineering manager"
    },
    {
      "name": "CI Bot",
      "email": "ci@company.com",
      "role": "bot",
      "notes": "Jenkins CI system"
    }
  ]
}
```

### repos.json
```json
{
  "repos": [
    {
      "name": "payments-service",
      "path": "/Users/aayush/code/payments-service",
      "platform": "bitbucket",
      "remote_id": "team/payments-service",
      "host": ""
    },
    {
      "name": "frontend-app",
      "path": "/Users/aayush/code/frontend-app",
      "platform": "bitbucket",
      "remote_id": "team/frontend-app",
      "host": "bitbucket.example.com"
    }
  ]
}
```

`host` is optional: empty or a cloud domain (`bitbucket.org` / `github.com`) means Cloud; any other value marks a self-hosted Bitbucket Server / GitHub Enterprise instance.

### rules.json
```json
{
  "rules": [
    {
      "name": "Ignore bot messages",
      "enabled": true,
      "condition": {
        "field": "actor.email",
        "operator": "contains",
        "value": "bot"
      },
      "action": "drop"
    },
    {
      "name": "Ignore Jira status-only changes",
      "enabled": true,
      "condition": {
        "field": "source.raw_event_type",
        "operator": "equals",
        "value": "issue_status_changed"
      },
      "action": "drop"
    },
    {
      "name": "Mute #random channel",
      "enabled": true,
      "condition": {
        "all": [
          {"field": "source.platform", "operator": "equals", "value": "slack"},
          {"field": "content.metadata.slack_channel", "operator": "equals", "value": "random"}
        ]
      },
      "action": "drop"
    }
  ]
}
```
