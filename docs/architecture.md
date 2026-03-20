# Laya: System Architecture

## 1. Overview

Laya is a **local-first AI operating system** for professionals. It intercepts events from a user's professional tools (Jira, Bitbucket, Slack, Gmail, Calendar), performs autonomous research and action-staging using LLM-powered agents, and presents the user with ready-to-approve **Action Cards** in a desktop application.

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
+----------------+    HTTP POST    +--------------------+
|      n8n       | --------------> |    Laya Engine     |
|   (Docker)     |   Laya Event    |    (Python)        |
|                | <-------------- |                    |
|  Normalize     |   Action Req    |  Classify          |
|  events        |                 |  Research          |
|                |                 |  Stage             |
|  Execute       |                 |  Learn             |
|  actions       |                 +---------+----------+
+----------------+                           |
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
| **Laya Engine** | Python (FastAPI + LangGraph) | Orchestration, LLM calls, memory, agent management |
| **n8n** | Local Node.js process (npm) | Sole gateway to external services (both inbound and outbound) |

## 3. Detailed Architecture

```
+---------------------------------------------------------------------------+
|                           USER'S MACHINE                                  |
|                                                                           |
|  +- TAURI v2 APP SHELL -----------------------------------------------+   |
|  |                                                                    |   |
|  |  +- Svelte Frontend --------------------------------------------+  |   |
|  |  |                                                              |  |   |
|  |  |  +- Dashboard -+    +- Feed ----------+  +- Chat ---------+  |  |   |
|  |  |  | Stats,      |    | Action Cards    |  | Conversational |  |  |   |
|  |  |  | Analytics,  |    | sorted by       |  | interface to   |  |  |   |
|  |  |  | Time saved, |    | priority/time   |  | query context  |  |  |   |
|  |  |  | LLM costs   |    |                 |  |                |  |  |   |
|  |  |  +-------------+    | Simple: approve |  +----------------+  |  |   |
|  |  |                     |  from feed      |                      |  |   |
|  |  |  +- Workspace ----+ |                 |  +- Settings -----+  |  |   |
|  |  |  | Timeline,      | | Complex: open   |  | Models, keys,  |  |  |   |
|  |  |  | Live Agent,    | |  workspace      |  | repos, team,   |  |  |   |
|  |  |  | Context,       | |                 |  | rules, privacy |  |  |   |
|  |  |  | Staged Outputs | +-----------------+  +----------------+  |  |   |
|  |  |  +----------------+                                          |  |   |
|  |  +----------------------------+---------------------------------+  |   |
|  |                               | WebSocket (bidirectional)          |   |
|  |  Rust: System tray, native    |                                    |   |
|  |  notifications, sidecar mgmt  |                                    |   |
|  +-------------------------------+------------------------------------+   |
|                                  |                                        |
|  +- LAYA ENGINE (Python) --------+----------------------------------+     |
|  |                                                                  |     |
|  |  +- FastAPI Server -------------------------------------------+  |     |
|  |  |  POST /events          (receives from n8n)                 |  |     |
|  |  |  POST /actions/approve (receives from UI)                  |  |     |
|  |  |  GET  /cards           (UI fetches feed)                   |  |     |
|  |  |  GET  /dashboard       (UI fetches stats)                  |  |     |
|  |  |  WS   /ws              (real-time bidirectional)           |  |     |
|  |  |  GET  /health          (health check)                      |  |     |
|  |  |  CRUD /settings        (configuration)                     |  |     |
|  |  +------------------------------------------------------------+  |     |
|  |                                                                  |     |
|  |  +- LangGraph Orchestration ----------------------------------+  |     |
|  |  |                                                            |  |     |
|  |  | INGEST -> RULES -> ROUTER -> WORKER(s) -> STAGER -> EMIT   |  |     |
|  |  |                      |                                     |  |     |
|  |  |           +----------+----------+                          |  |     |
|  |  |           |      LiteLLM        |                          |  |     |
|  |  |           |  Router: Haiku      |                          |  |     |
|  |  |           |  Stager: Sonnet     |                          |  |     |
|  |  |           |  Local: Ollama      |                          |  |     |
|  |  |           +---------------------+                          |  |     |
|  |  |                                                            |  |     |
|  |  |  Workers:                                                  |  |     |
|  |  |    ENGINEER -> Coding Agent (Claude Code/Gemini/Codex PTY) |  |     |
|  |  |    COMMS    -> LLM drafting with memory context            |  |     |
|  |  |    OPS      -> Calendar/event aggregation + LLM synthesis  |  |     |
|  |  +------------------------------------------------------------+  |     |
|  |                                                                  |     |
|  |  +- Internal Tools (Python functions) ------------------------+  |     |
|  |  |  memory_search | event_lookup | entity_lookup | entity_link|  |     |
|  |  |  team_lookup   | card_history | feedback_query             |  |     |
|  |  +------------------------------------------------------------+  |     |
|  |                                                                  |     |
|  |  +- Scheduled Jobs -------------------------------------------+  |     |
|  |  |  Daily Briefing (cron) | Memory cleanup (weekly)           |  |     |
|  |  +------------------------------------------------------------+  |     |
|  +------------------------------------------------------------------+     |
|          |                  |                    |                        |
|          | HTTP             | Read/Write         | Embed/Query            |
|          v                  v                    v                        |
|  +--------------+   +---------------+   +-------------------+             |
|  |     n8n      |   |   SQLite      |   |    ChromaDB       |             |
|  |  (Docker)    |   |               |   |   (embedded)      |             |
|  |              |   | events        |   |                   |             |
|  | Ingestion:   |   | action_cards  |   | laya_memory       |             |
|  |  Jira        |   | action_log    |   | collection        |             |
|  |  Bitbucket   |   | entities      |   |                   |             |
|  |  Slack       |   | workspace_*   |   | nomic-embed       |             |
|  |  Gmail       |   | audit_log     |   | (local)           |             |
|  |  Calendar    |   |               |   |                   |             |
|  |              |   +---------------+   +-------------------+             |
|  | Execution:   |                                                         |
|  |  Create PR   |   +---------------+    +------------------+             |
|  |  Send email  |   | Config        |    | Coding Agent     |             |
|  |  Post Slack  |   | Files         |    | (PTY subprocess) |             |
|  |  Add comment |   |               |    |                  |             |
|  |  Update Jira |   | settings.json |    | Claude Code  OR  |             |
|  |              |   | team.json     |    | Gemini CLI   OR  |             |
|  +--------------+   | repos.json    |    | Codex CLI        |             |
|                     | rules.json    |    |                  |             |
|                     +---------------+    +------------------+             |
|                                                                           |
|  +- Optional -------------------------------------------------------+     |
|  |  Ollama (local LLMs for privacy-sensitive processing)            |     |
|  +------------------------------------------------------------------+     |
|                                                                           |
+---------------------------------------------------------------------------+
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

### n8n Workflow Inventory (v0.1)

| Workflow | Type | Trigger | Action |
|---|---|---|---|
| jira-ingestion | Inbound | Jira webhook (ticket events) | Normalize + POST to engine |
| jira-executor | Outbound | Engine webhook | Add comment, update ticket |
| bitbucket-ingestion | Inbound | Bitbucket webhook (PR/build events) | Normalize + POST to engine |
| bitbucket-executor | Outbound | Engine webhook | Create PR, add comment |
| slack-ingestion | Inbound | Slack events (messages, mentions) | Normalize + POST to engine |
| slack-executor | Outbound | Engine webhook | Send message, reply to thread |
| gmail-ingestion | Inbound | Gmail trigger (new emails) | Normalize + POST to engine |
| gmail-executor | Outbound | Engine webhook | Send/reply to email |
| calendar-ingestion | Inbound | Calendar trigger (upcoming/new events) | Normalize + POST to engine |
| calendar-executor | Outbound | Engine webhook | Create/update calendar event |

### Key Design Properties

- **n8n holds all external service credentials.** Laya Engine never needs OAuth tokens for Jira, Slack, etc.
- **n8n is a "dumb pipe."** No classification, no context extraction, no AI logic. Pure structural normalization.
- **Workflows are pre-built and shipped with Laya** as JSON exports, auto-imported on first launch.

## 5. Laya Engine: The Orchestration Layer

The engine is a Python process running FastAPI (HTTP + WebSocket server) and LangGraph (orchestration state machine).

### LangGraph State Machine (Event Mode)

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
+-------------+
|   ROUTER    |  LLM call (fast model via LiteLLM):
|             |  - Classify: category, persona, priority
| (Haiku /    |  - Extract entities (ticket IDs, file paths, people)
|  Flash)     |  - Generate research_plan
|             |  - Determine: requires_research? secondary_persona?
|             |  - Inject learning feedback from past approvals
|             |  - Query ChromaDB for related past events
+------+------+
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
+-------------+
|   STAGER    |  LLM call (strong model via LiteLLM):
|             |  Synthesize research + drafts into polished Action Card
| (Sonnet /   |  Generate suggested_actions array
|  Opus)      |
+------+------+
       |
       v
+-------------+
|    EMIT     |  Pure code:
|             |  - Store Action Card in SQLite
|             |  - Embed card summary in ChromaDB
|             |  - Update entity cross-references
|             |  - Log to audit_log
|             |  - Push to UI via WebSocket
+------+------+
       |
       v
Tauri UI shows Action Card
       |
       | (user clicks Approve)
       v
Engine POSTs action to n8n --> n8n executes --> result stored
```

### LangGraph State Machine (Chat Mode)

```
User types question
       |
       v
PARSE INTENT (fast LLM)
  - Identify: status_query, context_lookup, card_reference, etc.
  - Extract: entities, time ranges
       |
       v
RETRIEVE CONTEXT
  - memory_search (ChromaDB) for semantic matches
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

### LangGraph State Object

```python
class LayaState(TypedDict):
    # Input
    raw_event: LayaEvent
    enriched_actor: ActorWithRelationship

    # Rules output
    rules_result: Literal["pass", "drop"]

    # Router output
    classification: Classification
    research_plan: list[str]
    related_events: list[dict]

    # Worker output
    research_findings: list[ResearchFinding]
    drafted_outputs: list[DraftedOutput]

    # Stager output
    action_card: ActionCard

    # Metadata
    processing_start: datetime
    processing_log: list[str]
    error: Optional[str]
```

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

Default: `nomic-embed` or `all-MiniLM` via `sentence-transformers` (local). Architecture supports swapping to cloud embedding APIs via configuration.

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

BACKEND (Python 3.11+)
  - FastAPI (HTTP + WebSocket server)
  - LangGraph (orchestration state machine)
  - LiteLLM (unified LLM interface)
  - ChromaDB (embedded vector store)
  - sentence-transformers (local embeddings)
  - aiosqlite (async SQLite)
  - asyncio + pty (agent subprocess management)

INFRASTRUCTURE
  - Tauri v2 / Rust (app shell, sidecar management)
  - n8n / local npm (integration gateway)
  - Ollama (optional, local LLMs)

DATA
  - SQLite (~/.laya/data/laya.db)
  - ChromaDB (~/.laya/data/chromadb/)
  - JSON configs (~/.laya/*.json)

CODING AGENTS (user configures one)
  - Claude Code CLI
  - Gemini CLI
  - OpenAI Codex CLI

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
