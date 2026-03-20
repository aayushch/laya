# Laya Project Structure

## Repository Layout

```
laya/
|-- README.md
|-- LICENSE
|-- package.json                          # n8n installed via npm (local node_modules)
|
|-- engine/                               # Python backend
|   |-- pyproject.toml                    # dependencies (poetry/pdm)
|   |-- laya/
|   |   |-- __init__.py
|   |   |-- main.py                       # FastAPI app + startup
|   |   |-- config.py                     # settings loader (reads ~/.laya/*.json)
|   |   |
|   |   |-- api/                          # HTTP + WebSocket endpoints
|   |   |   |-- __init__.py
|   |   |   |-- events.py                 # POST /events (receives from n8n)
|   |   |   |-- cards.py                  # GET/POST card endpoints
|   |   |   |-- dashboard.py             # GET /dashboard (analytics)
|   |   |   |-- settings_api.py          # CRUD /settings
|   |   |   |-- health.py                # GET /health
|   |   |   |-- chat.py                  # POST /chat
|   |   |   |-- websocket.py             # WS /ws handler
|   |   |   |-- actions.py               # POST /actions/approve, /actions/dismiss
|   |   |   |-- workspace_api.py         # GET /cards/:id/workspace
|   |   |
|   |   |-- graph/                        # LangGraph orchestration
|   |   |   |-- __init__.py
|   |   |   |-- state.py                  # LayaState TypedDict
|   |   |   |-- graph.py                  # Main graph definition + compilation
|   |   |   |-- ingest.py                # INGEST node
|   |   |   |-- rules.py                 # RULES ENGINE node
|   |   |   |-- router.py                # ROUTER node (LLM classification)
|   |   |   |-- stager.py                # STAGER node (LLM card generation)
|   |   |   |-- emit.py                  # EMIT node (store + push to UI)
|   |   |   |-- chat_graph.py            # Chat mode graph (intent -> retrieve -> respond)
|   |   |
|   |   |-- workers/                      # Persona workers (LangGraph sub-graphs)
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # BaseWorker interface
|   |   |   |-- engineer.py              # ENGINEER worker (coding agent orchestration)
|   |   |   |-- comms.py                 # COMMS worker (draft replies)
|   |   |   |-- ops.py                   # OPS worker (calendar prep, briefings)
|   |   |
|   |   |-- agents/                       # Coding agent adapters
|   |   |   |-- __init__.py
|   |   |   |-- base.py                   # CodingAgent protocol + AgentSession protocol
|   |   |   |-- claude_code.py           # Claude Code CLI adapter
|   |   |   |-- gemini_cli.py            # Gemini CLI adapter
|   |   |   |-- codex_cli.py             # OpenAI Codex CLI adapter
|   |   |   |-- session_manager.py       # Manages active PTY sessions across cards
|   |   |
|   |   |-- tools/                        # Internal tool functions (used by LangGraph)
|   |   |   |-- __init__.py
|   |   |   |-- memory.py                # memory_search(query) -> ChromaDB similarity search
|   |   |   |-- events.py                # event_lookup(subject_id, time_range) -> SQLite query
|   |   |   |-- entities.py              # entity_lookup(id), entity_link(a, b) -> SQLite CRUD
|   |   |   |-- team.py                  # team_lookup(email) -> team.json lookup
|   |   |   |-- feedback.py              # feedback_query(event_type) -> past approval patterns
|   |   |   |-- cards.py                 # card_history(subject_id, status) -> SQLite query
|   |   |
|   |   |-- db/                           # Database layer
|   |   |   |-- __init__.py
|   |   |   |-- sqlite.py                # SQLite connection pool + query helpers
|   |   |   |-- chromadb_store.py        # ChromaDB connection + embed/query operations
|   |   |   |-- migrations/              # SQL migration files (numbered)
|   |   |   |   |-- 001_initial.sql      # Core tables: events, action_cards, action_log
|   |   |   |   |-- 002_entities.sql     # Entity resolution: entities table
|   |   |   |   |-- 003_workspace.sql    # Workspace: workspace_sessions, workspace_events
|   |   |   |   |-- 004_audit.sql        # Audit: audit_log table
|   |   |   |   |-- ...
|   |   |   |-- migrate.py               # Migration runner (check version, apply pending)
|   |   |
|   |   |-- scheduler/                    # Scheduled/cron jobs
|   |   |   |-- __init__.py
|   |   |   |-- briefing.py              # Daily briefing generator
|   |   |   |-- cleanup.py               # 90-day memory cleanup
|   |   |   |-- scheduler.py             # Job scheduler (APScheduler or custom)
|   |   |
|   |   |-- security/                     # Security utilities
|   |   |   |-- __init__.py
|   |   |   |-- keychain.py              # OS keychain read/write (macOS/Linux/Windows)
|   |   |   |-- tiers.py                 # Data tier classification logic
|   |   |   |-- prompt_safety.py         # Input delimiting for LLM prompts
|   |   |
|   |   |-- models/                       # Data models / types
|   |   |   |-- __init__.py
|   |   |   |-- event.py                 # LayaEvent pydantic model
|   |   |   |-- card.py                  # ActionCard pydantic model
|   |   |   |-- action.py               # Action pydantic models
|   |   |   |-- workspace.py            # WorkspaceSession, WorkspaceEvent models
|   |   |   |-- classification.py       # Classification, ResearchPlan models
|   |   |
|   |   |-- llm/                          # LLM interaction layer
|   |       |-- __init__.py
|   |       |-- client.py                # LiteLLM wrapper with model selection from config
|   |       |-- prompts/                 # Prompt templates
|   |           |-- router.py            # Router classification prompt
|   |           |-- stager.py            # Stager card generation prompt
|   |           |-- engineer.py          # Engineer worker prompt builder
|   |           |-- comms.py             # Comms worker prompt builder
|   |           |-- ops.py               # Ops worker prompt builder
|   |           |-- chat.py              # Chat intent + response prompts
|   |           |-- briefing.py          # Daily briefing prompt
|   |
|   |-- tests/
|   |   |-- __init__.py
|   |   |-- conftest.py                  # Shared fixtures, mock LLM responses
|   |   |-- unit/
|   |   |   |-- test_ingest.py           # INGEST node tests
|   |   |   |-- test_rules.py            # Rules engine tests
|   |   |   |-- test_router.py           # Router classification tests (mocked LLM)
|   |   |   |-- test_stager.py           # Stager output tests (mocked LLM)
|   |   |   |-- test_entities.py         # Entity resolution tests
|   |   |   |-- test_tools.py            # Internal tools tests
|   |   |   |-- test_tiers.py            # Data tier classification tests
|   |   |   |-- test_prompt_safety.py    # Prompt injection defense tests
|   |   |   |-- test_models.py           # Pydantic model validation tests
|   |   |   |-- ...
|   |   |-- integration/
|   |   |   |-- test_event_flow.py       # Full event pipeline (mocked LLM + real DB)
|   |   |   |-- test_agent_session.py    # Coding agent subprocess tests (test repo)
|   |   |   |-- test_n8n_comms.py        # Engine <-> n8n HTTP tests (mocked n8n)
|   |   |   |-- test_workspace.py        # Workspace state persistence tests
|   |   |   |-- ...
|   |   |-- fixtures/
|   |       |-- events/                  # Sample Laya Event JSONs per platform
|   |       |   |-- jira_bug_assigned.json
|   |       |   |-- bitbucket_pr_review.json
|   |       |   |-- slack_mention.json
|   |       |   |-- gmail_new_email.json
|   |       |   |-- calendar_upcoming.json
|   |       |-- llm_responses/           # Mocked LLM outputs for deterministic tests
|   |           |-- router_engineer.json
|   |           |-- router_comms.json
|   |           |-- stager_card.json
|   |
|   |-- build/
|       |-- pyinstaller.spec             # PyInstaller config for bundling
|       |-- build.sh                     # Build script for engine binary
|
|-- ui/                                   # Tauri + Svelte frontend
|   |-- src-tauri/                        # Rust (Tauri shell)
|   |   |-- Cargo.toml
|   |   |-- tauri.conf.json              # Tauri config: windows, permissions, updater, sidecar
|   |   |-- src/
|   |   |   |-- main.rs                  # Tauri app entry point
|   |   |   |-- lib.rs                   # Tauri command handlers
|   |   |   |-- tray.rs                  # System tray setup + badge management
|   |   |   |-- sidecar.rs              # Python engine process lifecycle management
|   |   |   |-- n8n.rs                 # n8n process lifecycle management (npm install + start)
|   |   |   |-- notifications.rs         # Native OS notification bridge
|   |   |   |-- health.rs               # Periodic health check polling
|   |   |-- icons/                       # App icons per platform
|   |   |-- capabilities/               # Tauri v2 capability definitions
|   |
|   |-- src/                              # Svelte frontend
|   |   |-- app.html                     # HTML entry point
|   |   |-- app.css                      # Global styles + Tailwind imports
|   |   |
|   |   |-- lib/
|   |   |   |-- stores/                  # Svelte stores (reactive state management)
|   |   |   |   |-- cards.ts             # Action Cards state (feed data)
|   |   |   |   |-- workspace.ts         # Active workspace state (per card)
|   |   |   |   |-- chat.ts              # Chat message history
|   |   |   |   |-- dashboard.ts         # Analytics/dashboard data
|   |   |   |   |-- settings.ts          # App configuration state
|   |   |   |   |-- health.ts            # System health state
|   |   |   |   |-- notifications.ts     # Notification queue
|   |   |   |
|   |   |   |-- api/                     # Engine communication layer
|   |   |   |   |-- rest.ts              # REST API client (fetch wrapper)
|   |   |   |   |-- websocket.ts         # WebSocket manager (connect, reconnect, dispatch)
|   |   |   |   |-- types.ts             # TypeScript types matching API contracts
|   |   |   |
|   |   |   |-- components/              # Reusable UI components
|   |   |   |   |-- feed/
|   |   |   |   |   |-- ActionCard.svelte         # Card in feed view (compact)
|   |   |   |   |   |-- CardList.svelte           # Scrollable card feed
|   |   |   |   |   |-- PriorityBadge.svelte      # CRITICAL/HIGH/MEDIUM/LOW badge
|   |   |   |   |   |-- StatusIndicator.svelte    # Card status icon + label
|   |   |   |   |   |-- FilterBar.svelte          # Feed filter controls
|   |   |   |   |
|   |   |   |   |-- workspace/
|   |   |   |   |   |-- WorkspaceLayout.svelte    # Three-panel workspace layout
|   |   |   |   |   |-- Timeline.svelte           # Chronological event timeline
|   |   |   |   |   |-- TimelineEvent.svelte      # Single timeline entry
|   |   |   |   |   |-- LiveAgent.svelte          # Agent output + approval panel
|   |   |   |   |   |-- ApprovalPrompt.svelte     # Agent approval request UI
|   |   |   |   |   |-- ContextSidebar.svelte     # Related entities, cross-refs, team
|   |   |   |   |   |-- StagedOutput.svelte       # Final output display (diff, draft, etc.)
|   |   |   |   |   |-- CodeDiff.svelte           # Syntax-highlighted code diff
|   |   |   |   |   |-- SessionControls.svelte    # Pause, resume, cancel buttons
|   |   |   |   |
|   |   |   |   |-- dashboard/
|   |   |   |   |   |-- DashboardLayout.svelte    # Dashboard grid layout
|   |   |   |   |   |-- StatCard.svelte           # Single metric card
|   |   |   |   |   |-- SourceBreakdown.svelte    # Events by source chart
|   |   |   |   |   |-- ApprovalRates.svelte      # Approval rate by persona chart
|   |   |   |   |   |-- TimeSaved.svelte          # Running time-saved counter
|   |   |   |   |
|   |   |   |   |-- chat/
|   |   |   |   |   |-- ChatPanel.svelte          # Chat sidebar container
|   |   |   |   |   |-- ChatMessage.svelte        # Single chat message bubble
|   |   |   |   |   |-- ChatInput.svelte          # Text input + send button
|   |   |   |   |
|   |   |   |   |-- settings/
|   |   |   |   |   |-- SettingsLayout.svelte     # Settings page tabs
|   |   |   |   |   |-- ModelConfig.svelte        # LLM model selection
|   |   |   |   |   |-- SourceConnections.svelte  # Connected services status
|   |   |   |   |   |-- TeamEditor.svelte         # team.json visual editor
|   |   |   |   |   |-- RepoConfig.svelte         # repos.json visual editor
|   |   |   |   |   |-- RulesEditor.svelte        # rules.json visual editor
|   |   |   |   |   |-- PrivacySettings.svelte    # Privacy tier configuration
|   |   |   |   |   |-- AgentConfig.svelte        # Coding agent selection
|   |   |   |   |   |-- AuditLogViewer.svelte     # Audit log browser
|   |   |   |   |
|   |   |   |   |-- setup/
|   |   |   |   |   |-- SetupWizard.svelte        # First-run wizard container
|   |   |   |   |   |-- StepLLM.svelte            # Step 1: LLM configuration
|   |   |   |   |   |-- StepConnect.svelte        # Step 2: Connect tools
|   |   |   |   |   |-- StepAgent.svelte          # Step 3: Coding agent + repos
|   |   |   |   |   |-- StepTeam.svelte           # Step 4: Team config
|   |   |   |   |   |-- StepFilters.svelte        # Step 5: Event filters
|   |   |   |   |
|   |   |   |   |-- common/
|   |   |   |       |-- LoadingSpinner.svelte
|   |   |   |       |-- EmptyState.svelte
|   |   |   |       |-- ErrorDisplay.svelte
|   |   |   |       |-- HealthBadge.svelte        # Green/yellow/red system status
|   |   |   |
|   |   |   |-- utils/
|   |   |       |-- time.ts                       # Time formatting helpers
|   |   |       |-- priority.ts                   # Priority color/sort helpers
|   |   |
|   |   |-- routes/                      # SvelteKit pages
|   |       |-- +layout.svelte           # Main layout: sidebar nav + content area + chat
|   |       |-- +page.svelte             # Home: Dashboard + Feed
|   |       |-- workspace/
|   |       |   |-- [cardId]/
|   |       |       |-- +page.svelte     # Card workspace view
|   |       |-- settings/
|   |       |   |-- +page.svelte         # Settings page
|   |       |-- setup/
|   |           |-- +page.svelte         # First-run wizard
|   |
|   |-- static/                          # Static assets
|   |   |-- favicon.png
|   |   |-- logo.svg
|   |
|   |-- package.json
|   |-- svelte.config.js
|   |-- tailwind.config.js
|   |-- vite.config.ts
|   |-- tsconfig.json
|   |
|   |-- tests/                           # Frontend tests
|       |-- components/                  # Component tests (Vitest + Testing Library)
|       |   |-- ActionCard.test.ts
|       |   |-- WorkspaceTimeline.test.ts
|       |   |-- ChatPanel.test.ts
|       |   |-- ...
|       |-- e2e/                         # End-to-end tests (Playwright)
|           |-- event-to-card.spec.ts    # Full flow: event arrives -> card appears
|           |-- workspace.spec.ts        # Workspace interaction flow
|           |-- chat.spec.ts             # Chat query flow
|           |-- setup-wizard.spec.ts     # First-run setup flow
|
|-- n8n/                                  # Pre-built n8n workflow definitions
|   |-- workflows/
|   |   |-- jira-ingestion.json          # Jira trigger -> normalize -> POST to engine
|   |   |-- jira-executor.json           # Webhook -> Jira API action
|   |   |-- bitbucket-ingestion.json
|   |   |-- bitbucket-executor.json
|   |   |-- slack-ingestion.json
|   |   |-- slack-executor.json
|   |   |-- gmail-ingestion.json
|   |   |-- gmail-executor.json
|   |   |-- calendar-ingestion.json
|   |   |-- calendar-executor.json
|   |-- import.sh                        # Script to import workflows into n8n via API
|
|-- scripts/                             # Development and build scripts
|   |-- dev.sh                           # Start all services in dev mode
|   |-- build.sh                         # Build for distribution (all platforms)
|   |-- test.sh                          # Run all test suites
|   |-- setup-dev.sh                     # One-time dev environment setup
|
|-- docs/                                # Documentation
|   |-- architecture.md                  # System architecture (this document set)
|   |-- event-schema.md                  # Laya Event schema specification
|   |-- api-contracts.md                 # Full API documentation
|   |-- project-structure.md             # This file
|   |-- implementation-plan.md           # Milestones and timeline
|   |-- decision-log.md                  # All architectural decisions with rationale
|
|-- .github/                             # GitHub CI/CD (if using GitHub)
    |-- workflows/
        |-- ci.yml                       # Run tests on PR
        |-- release.yml                  # Build + publish installers on tag
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
|   |-- laya.db                # SQLite database (events, cards, actions, entities, workspaces, audit)
|   |-- chromadb/              # ChromaDB persistence directory (embeddings)
|-- logs/
    |-- laya.log               # Application logs (rotating, max 50MB)
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
    "url": "http://localhost:5678",
    "webhooks": {
      "jira_executor": "/webhook/jira-exec-id",
      "bitbucket_executor": "/webhook/bb-exec-id",
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
