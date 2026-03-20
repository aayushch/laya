# Laya Decision Log

Every architectural and design decision made during the planning phase, with rationale.

## Phase 1: Scope & MVP Definition

| # | Decision | Rationale |
|---|---|---|
| 1 | **Engineering persona first** | Dogfoodable by the developer, MCP/coding agent tooling is most mature, Jira/Bitbucket triggers are well-understood. |
| 2 | **Local-first desktop app, single user** | No multi-tenancy, no auth system, no server infrastructure. Privacy by default. One user's event volume (~100-200/day) needs no scaling infrastructure. |
| 3 | **n8n owns all event ingestion** | No custom pollers or webhooks. n8n handles push/pull/latency per connector. Keeps Laya's ecosystem lightweight. Laya simply waits for n8n to fire a trigger. |
| 4 | **All 5 sources in v0.1** (Gmail, Slack, Bitbucket, Jira, Calendar) | Surface integration issues early. Proves the architecture generalizes across source types. Important since we need to build for multiple personas eventually. |
| 5 | **60-second event-to-card SLA** | Event fires from any source -> Action Card with context-aware staged response within 60 seconds. Concrete success criteria. |

## Phase 2: Product Surface & UX

| # | Decision | Rationale |
|---|---|---|
| 6 | **Tauri for app shell** | Lightweight (~5MB vs Electron's ~150MB). Native system tray + notifications. Web frontend (Svelte) for the UI. Best fit for an always-running background process. |
| 7 | **Dashboard + Feed from day one** | User wanted both a summary view (stats, analytics) and the action card feed from the start, not feed-first with dashboard added later. |
| 8 | **Execute approved actions via n8n** | n8n is the sole gateway in both directions. Laya Engine never calls external APIs directly. One place to manage credentials (n8n's credential store). One place to add new integrations. |
| 9 | **Chat sidebar in v0.1** | A conversational interface alongside the card feed for ad-hoc queries about events, context, and status. Requires two input paths in the Engine (event-driven + query-driven). |

## Phase 3: Ingestion Layer

| # | Decision | Rationale |
|---|---|---|
| 10 | **No pre-classification in n8n** | n8n doesn't have built-in AI classifiers. Static source->category mapping (Jira=CODE, Slack=COMMS) is unreliable for ambiguous sources. All classification happens in the Engine's Router via LLM. n8n is a dumb pipe. |
| 11 | **No context extraction in n8n** | n8n passes the raw body untouched. The Engine's LLM Router handles entity extraction (ticket IDs, file paths, mentions) as part of classification. Keeps n8n workflows simple. |
| 12 | **HTTP POST between n8n and engine** | Simplest option. No message queue needed at single-user volume. n8n POSTs normalized events to engine's local API. Engine POSTs approved actions back to n8n webhooks. |
| 13 | **Simple JSON team config (team.json)** | User maintains a file with team member names, emails, and roles (manager/teammate/external/bot). Used for actor relationship resolution and priority calibration. ~5 minutes of setup. |
| 14 | **n8n owns all external service credentials** | Platform OAuth tokens live in n8n's encrypted credential store. Laya Engine only needs LLM API keys. Single credential management point. |

## Phase 4: Orchestration Layer

| # | Decision | Rationale |
|---|---|---|
| 15 | **All LLM choices configurable** | Router model, Stager model, coding agent -- all user-selectable in settings. Supports cloud models (Anthropic, OpenAI, Google) and Ollama for local. Future-proofs against model changes. |
| 16 | **Stronger model for Stager, fast model for Router** | Router does narrow classification (cheap/fast model suffices). Stager produces user-facing output (code diffs, drafted emails) where quality matters. |
| 17 | **Coding agents for codebase research** | ENGINEER Worker delegates to Claude Code / Gemini CLI / Codex CLI as sub-agents. No need to build custom MCP codebase tooling -- these agents already have sophisticated file navigation, code search, git integration built in. |
| 18 | **Local repos + API fallback via n8n** | User configures local repo paths in repos.json. Coding agent works locally for speed. Falls back to Bitbucket/GitHub API via n8n if repo not cloned locally. |
| 19 | **Sequential multi-Worker execution** | When an event needs multiple personas (e.g., Slack message needing code investigation + reply), Workers run in sequence. Research findings flow into the next Worker as context. Ensures the drafted reply includes technical analysis. |

## Phase 5: Memory & Context Layer

| # | Decision | Rationale |
|---|---|---|
| 20 | **Dual storage: SQLite + ChromaDB** | SQLite for structured data (events, cards, actions, entities) with exact lookups. ChromaDB for semantic search (embeddings of content, research, chat) with similarity matching. Different query patterns need different stores. |
| 21 | **Three-layer entity resolution** | Layer 1: Explicit links from platform data (deterministic). Layer 2: Semantic similarity from ChromaDB embeddings. Layer 3: LLM confirmation before creating new entity links. Balances accuracy with false-positive prevention. |
| 22 | **Local embeddings first (nomic-embed), cloud-ready** | Default to nomic-embed or all-MiniLM via sentence-transformers (local, private, no API cost). Architecture supports swapping to cloud embedding APIs (OpenAI, Voyage) via configuration. |
| 23 | **90-day active memory window** | Events and embeddings retained for 90 days actively. Entity records and Action Cards kept indefinitely (they're small and valuable). Prevents unbounded storage growth. |
| 24 | **Top-3 context retrieval per LLM call** | When injecting context into LLM calls, retrieve max 3 related past events ranked by relevance + recency. Keeps token budget ~5000 tokens total, well within any model's context window. |

## Phase 6: MCP & Tool Layer

| # | Decision | Rationale |
|---|---|---|
| 25 | **Hybrid tool architecture** | Internal tools (memory, DB queries) as Python functions in LangGraph -- fast, simple, no overhead. Coding agents as CLI subprocesses. n8n as HTTP. MCP protocol adds value for cross-agent interop but isn't needed for internal tools in v0.1. |
| 26 | **Context injection via prompt to coding agents** | Workers query Laya's internal tools first, then pass results to coding agents via prompt text. Coding agents don't need direct access to Laya's databases. Clean separation of concerns. |
| 27 | **User configures one default coding agent** | Support three agents: Claude Code, Gemini CLI, OpenAI Codex CLI. User picks one in settings. All coding tasks go to that agent. Thin adapter abstraction with common interface. |
| 28 | **READ is automatic, WRITE requires approval** | All tools can read freely (events, memory, files). Any write to external systems (create PR, send email) goes through the Action Card approval gate. The UI IS the human-in-the-loop control. |

## Phase 7: Security Architecture

| # | Decision | Rationale |
|---|---|---|
| 29 | **OS keychain for API keys** | LLM API keys stored in macOS Keychain / Linux libsecret / Windows Credential Manager. Never in plaintext on disk. Platform OAuth tokens managed by n8n's own encrypted credential store. |
| 30 | **Three-tier data classification** | Tier 1 (metadata): always safe for cloud. Tier 2 (work content): default cloud. Tier 3 (DMs, email bodies, flagged content): cloud with privacy warning on the Action Card. User makes conscious tradeoff between quality and privacy. |
| 31 | **No PII detection in v0.1** | Rely on tier classification as the primary privacy control. PII scanning (regex or Presidio) deferred to v0.2 to reduce scope. |
| 32 | **Prompt injection defense via structural delimiting** | Untrusted event content wrapped in `[EVENT CONTENT - UNTRUSTED INPUT]` delimiters in prompts. No autonomous writes possible -- all writes require card approval. Output validated against JSON schema. |
| 33 | **Full audit trail in SQLite** | Every processing step logged with: model used, processing tier, token counts, latency, errors. Supports debugging, cost tracking, and privacy auditing. |
| 34 | **Coding agents use their own permission models** | Claude Code, Gemini CLI, and Codex CLI have built-in approval prompts for writes. No additional sandboxing. Combined with Action Card approval gate, this provides two layers of human approval. |

## Phase 8: Additional Features

| # | Decision | Rationale |
|---|---|---|
| 35 | **Card Workspace model** | Each Action Card can open into a full stateful workspace with timeline, live agent panel, context sidebar, and staged outputs. Necessary because real workflows (bug fix, code review) involve multiple approval steps within a single card. |
| 36 | **Interactive agent sessions via PTY** | Coding agents run as PTY subprocesses. Their stdout is streamed to the workspace timeline. Their approval requests are intercepted and surfaced in the workspace UI. User responses piped back to agent stdin. |
| 37 | **State persistence per workspace** | All workspace interactions logged to SQLite (workspace_sessions + workspace_events tables). State survives navigation between cards, app minimize/restore, and app restarts. |
| 38 | **WebSocket for real-time UI communication** | Bidirectional WebSocket between Engine and Tauri UI for streaming agent progress, approval requests, card updates, and user responses. HTTP too latent for real-time agent interaction. |
| 39 | **Simple cards remain one-click from feed** | Low-complexity cards (Slack reply, calendar prep) can be approved directly from the feed without opening a workspace. Only complex multi-step cards need the full workspace. Card type determined by Router's classification. |
| 40 | **Daily Briefing in v0.1** | Scheduled morning summary of overnight activity, pending cards, and today's calendar with context. Low effort given existing architecture. Demonstrates cross-source intelligence. |
| 41 | **Learning Loop in v0.1** | Store approval/edit/dismiss feedback in action_cards table. Inject as prompt context for future Router calls ("for similar events, user lowered priority 3/5 times"). Deferred ML-based learning to v0.2. |
| 42 | **Analytics Dashboard in v0.1** | Event counts, card stats, approval rates, time saved, LLM cost. All data already in SQLite from audit_log. Dashboard was already decided as part of the UI (Decision #7). Just needs aggregate queries + charts. |
| 43 | **User-Defined Rules/Filters in v0.1** | Essential for noise management. Rule engine sits between INGEST and ROUTER. Evaluates user-configured conditions (rules.json) against events. Drop or modify matching events. Without this, users drown in low-value cards. |

## Phase 9: Tech Stack

| # | Decision | Rationale |
|---|---|---|
| 44 | **Python 3.11+ for engine** | Required by LangGraph (Python-native). Mature ecosystem for AI/ML (sentence-transformers, ChromaDB, LiteLLM). asyncio task groups in 3.11+. |
| 45 | **LiteLLM for unified LLM interface** | Single `completion()` call works with 100+ providers (Anthropic, OpenAI, Google, Ollama). Perfectly implements configurable model settings without provider-specific code. |
| 46 | **Svelte + Skeleton + Tailwind for frontend** | Lightweight, fast, clean DX. Skeleton for accessible UI components. Tailwind for utility-first styling. Excellent Tauri integration. Layerchart or Chart.js for dashboard charts. |
| 47 | **Tauri v2 manages everything** | Single install for the user. Tauri launches Python engine as sidecar process, installs and manages n8n via npm. Native system tray + notifications. Auto-update support built in. |
| 48 | **n8n via npm, pre-built workflows** | n8n is installed locally via npm (no Docker required). 10 workflow JSON files (5 ingestion + 5 execution) shipped with Laya, auto-imported on first launch. Two-attempt install strategy: first tries full native addons, falls back to skip native compilation if node-gyp fails. |
| 49 | **SQLite + ChromaDB embedded** | All data stored locally in `~/.laya/`. No external database servers. SQLite for structured data, ChromaDB (embedded mode) for vector search. Zero-config persistence. |
| 50 | **pytest + Vitest + Playwright for testing** | pytest + pytest-asyncio for Python engine. Vitest + Svelte Testing Library for frontend components. Playwright for end-to-end browser tests through Tauri's webview. |

## Phase 10: Deployment

| # | Decision | Rationale |
|---|---|---|
| 51 | **Bundled embedded Python runtime** | Use PyInstaller/PyOxidizer to package the engine + all Python dependencies as a self-contained binary (~200MB). User never needs Python installed. No dependency conflicts. |
| 52 | **All three platforms from day one** | macOS, Linux, and Windows supported in v0.1. Primary development on macOS. CI/CD builds for all three. Tripled QA effort accepted for maximum reach. |
| 53 | **Single-file installer per platform** | DMG/PKG (macOS), AppImage/deb (Linux), MSI (Windows). Installer checks for Docker and guides user to install it if missing. |
| 54 | **Tauri auto-update** | Built-in update checking with stable/beta channels. Non-intrusive notification when update available. Database migrations run automatically on version upgrade. |
| 55 | **5-step first-run setup wizard** | Step 1: LLM config. Step 2: Connect tools (via n8n OAuth). Step 3: Coding agent + repos. Step 4: Team config. Step 5: Event filters. Gets user to a working state in ~5 minutes. |
| 56 | **SQL migration system for schema evolution** | Numbered migration files (001_initial.sql, 002_entities.sql, etc.). Engine checks schema version on startup and runs pending migrations. Simple, no heavy ORM framework. |
| 57 | **System tray with health status indicator** | Green (all healthy), yellow (degraded), red (engine/n8n offline). Badge count of pending cards. Click to open app. Minimizes to tray on close. Background operation. |
