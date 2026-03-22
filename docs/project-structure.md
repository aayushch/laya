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
|   |   |-- api/                          # HTTP + WebSocket endpoints (17 routers)
|   |   |   |-- __init__.py
|   |   |   |-- events.py                 # POST /events (receives from n8n)
|   |   |   |-- cards_api.py              # Card CRUD, grouping, archive/reopen
|   |   |   |-- actions_api.py            # POST /actions/approve, /actions/dismiss
|   |   |   |-- workspace_api.py          # Workspace/agent session endpoints
|   |   |   |-- chat_api.py               # Chat conversations
|   |   |   |-- dashboard_api.py          # GET /dashboard (analytics)
|   |   |   |-- settings_api.py           # CRUD /settings
|   |   |   |-- spaces_api.py             # Space/source CRUD, bulk assignment
|   |   |   |-- connections_api.py         # Integration connection status
|   |   |   |-- rules_api.py              # Event filter rules
|   |   |   |-- audit_api.py              # Audit log queries
|   |   |   |-- diagnostics_api.py        # System diagnostics
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
|   |   |   |-- emit.py                   # EMIT step: store card, embed in ChromaDB, push via WebSocket
|   |   |   |-- executor.py               # Execute approved actions via n8n
|   |   |   |-- chat.py                   # Chat assistant pipeline
|   |   |   |-- workers.py                # Multi-persona LLM workers
|   |   |   |-- entity_resolution.py      # Cross-platform entity linking
|   |   |   |-- space_resolution.py       # Space/source identification
|   |   |   |-- feedback.py               # Learning loop (approval pattern tracking)
|   |   |   |-- summarize.py              # Daily briefing generation
|   |   |   |-- briefing.py               # Briefing content assembly
|   |   |
|   |   |-- workers/                      # Persona workers
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # BaseWorker interface
|   |   |   |-- engineer.py               # ENGINEER worker (coding agent orchestration)
|   |   |   |-- comms.py                  # COMMS worker (draft replies)
|   |   |   |-- ops.py                    # OPS worker (calendar prep, briefings)
|   |   |
|   |   |-- agents/                       # Coding agent adapters
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # CodingAgent protocol + AgentSession protocol
|   |   |   |-- claude_code.py            # Claude Code CLI adapter
|   |   |   |-- gemini_cli.py             # Gemini CLI adapter
|   |   |   |-- codex_cli.py              # OpenAI Codex CLI adapter
|   |   |   |-- session_manager.py        # Manages active sessions across cards
|   |   |
|   |   |-- llm/                          # LLM interaction layer
|   |   |   |-- __init__.py
|   |   |   |-- client.py                 # LiteLLM wrapper with model selection, retries, space overrides
|   |   |   |-- providers.py              # Custom model provider management
|   |   |   |-- prompts/                  # Prompt templates (router, stager, engineer, comms, ops, etc.)
|   |   |   |-- tools/                    # MCP tool definitions
|   |   |       |-- definitions.py        # Tool schemas
|   |   |       |-- card_tools.py         # Card query tools
|   |   |       |-- entity_tools.py       # Entity lookup/link tools
|   |   |       |-- event_tools.py        # Event query tools
|   |   |       |-- search_tools.py       # Memory/semantic search tools
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
|   |   |   |-- chunking.py              # Document chunking for embeddings
|   |   |   |-- migrate.py               # Migration runner (check version, apply pending)
|   |   |   |-- migrations/              # 26 numbered SQL migration files
|   |   |       |-- 001_initial.sql       # Core tables: events, action_cards, action_log
|   |   |       |-- 002_entities.sql      # Entity resolution: entities table
|   |   |       |-- ...
|   |   |       |-- 014_spaces.sql        # Spaces, sources, space_api_keys
|   |   |       |-- ...
|   |   |       |-- 026_chat_conversations.sql
|   |   |
|   |   |-- models/                       # Pydantic data models
|   |   |   |-- __init__.py
|   |   |   |-- event.py                  # LayaEvent model
|   |   |   |-- card.py                   # ActionCard model
|   |   |   |-- workspace.py              # WorkspaceSession, WorkspaceEvent models
|   |   |   |-- ...                       # Additional models (action, classification, etc.)
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
|   |   |   |   |-- feedFilters.ts        # Feed filter state
|   |   |   |   |-- feedSelection.ts      # Selected card state
|   |   |   |   |-- feedView.ts           # Feed view mode
|   |   |   |   |-- health.ts             # System health state
|   |   |   |   |-- recentCards.ts        # Recent card tracking
|   |   |   |   |-- setup.ts              # First-run setup state
|   |   |   |   |-- spaces.ts             # Space/source state
|   |   |   |   |-- theme.ts              # Theme (dark/light), persists to localStorage
|   |   |   |   |-- websocket.ts          # WebSocket connection state
|   |   |   |
|   |   |   |-- api/                      # Engine communication layer
|   |   |   |   |-- engine.ts             # REST API client (all endpoints)
|   |   |   |   |-- types.ts              # TypeScript types matching API contracts
|   |   |   |
|   |   |   |-- components/              # Reusable UI components
|   |   |       |-- feed/                 # ActionCard, CardGroup, CardDetail, FilterBar, etc.
|   |   |       |-- workspace/            # AgentPanel, Timeline, Context, StagedOutput, etc.
|   |   |       |-- dashboard/            # StatCard, charts, analytics components
|   |   |       |-- chat/                 # ChatPanel, ChatMessage, ChatInput
|   |   |       |-- settings/             # SettingsLayout, ModelConfig, SpacesConfig, etc.
|   |   |       |-- setup/                # SetupWizard steps
|   |   |       |-- common/               # LoadingSpinner, EmptyState, ErrorDisplay, etc.
|   |   |
|   |   |-- routes/                       # SvelteKit pages
|   |       |-- +layout.svelte            # Main layout: sidebar nav + content area + chat
|   |       |-- +page.svelte              # Home page
|   |       |-- feed/
|   |       |   |-- +page.svelte          # Action Card feed
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
|   |           |-- +page.svelte          # System status page
|   |
|   |-- package.json                      # Node dependencies (SvelteKit, Tauri CLI, Tailwind, etc.)
|   |-- svelte.config.js                  # SvelteKit config (static adapter, SPA fallback)
|   |-- vite.config.ts                    # Vite bundler config (Tailwind v4 plugin)
|   |-- tsconfig.json                     # TypeScript config (strict mode)
|
|-- n8n/                                  # Pre-built n8n workflow definitions
|   |-- workflows/
|   |   |-- gmail-ingestion.json
|   |   |-- gmail-executor.json
|   |   |-- slack-ingestion.json
|   |   |-- jira-ingestion.json
|   |   |-- github-ingestion.json
|   |   |-- bitbucket-ingestion.json
|   |   |-- google-calendar-ingestion.json
|   |   |-- google-calendar-executor.json
|   |   |-- outlook-email-ingestion.json
|   |   |-- outlook-email-executor.json
|   |   |-- outlook-imap-ingestion.json
|   |   |-- outlook-imap-executor.json
|   |   |-- outlook-calendar-ingestion.json
|   |   |-- outlook-calendar-executor.json
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
|   |-- engine.log             # Application logs (rotating, 10 MB x 5 files)
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
  "n8n": {
    "url": "http://localhost:45678",
    "webhooks": {
      "jira_executor": "/webhook/jira-exec-id",
      "slack_executor": "/webhook/slack-exec-id",
      "gmail_executor": "/webhook/gmail-exec-id",
      "calendar_executor": "/webhook/cal-exec-id"
    }
  },
  "engine": {
    "port": 8420,
    "log_level": "INFO"
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
      "remote_id": "team/payments-service"
    },
    {
      "name": "frontend-app",
      "path": "/Users/aayush/code/frontend-app",
      "platform": "bitbucket",
      "remote_id": "team/frontend-app"
    }
  ]
}
```

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
