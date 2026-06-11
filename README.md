# Laya: Your AI Command Center

![Demo](./laya.gif)

**A cadence for professional orchestration.**

Laya is an open-source, local-first AI notification command center that aggregates Slack, Gmail, GitHub, Jira, Notion, Outlook, and Calendar notifications — powered by local LLMs via Ollama and LM Studio, or cloud models like Claude and GPT with your own API keys. It intercepts events from your professional tools, performs autonomous research and action-staging using LLM-powered agents, and presents you with ready-to-approve **Action Cards** -- so the answer is ready before you open the notification.

**Works with:**

- Ollama and LM Studio (local LLMs)
- Claude models (Anthropic)
- GPT models (OpenAI)
- Gemini models (Google)
- Llama models and any OpenAI-compatible endpoint — via LiteLLM

**Aggregates:**

- Gmail
- Slack
- GitHub
- Bitbucket
- Jira
- Linear
- Notion
- Outlook (email and calendar)
- Google Calendar

## How It Works

```
Your Tools (Jira, Slack, Gmail, Bitbucket, Calendar)
         |
         v
      n8n (local Node.js) -- normalizes events
         |
         v
   Laya Engine (Python) -- classifies, researches, stages
         |
         v
    Laya UI (Tauri + Svelte) -- Action Cards you approve or dismiss
         |
         v
      n8n -- executes approved actions (creates PRs, sends replies, etc.)
```

## Key Features

- **Multi-persona brain:** Routes events to specialized AI personas (Engineer, Comms, Ops) with domain-specific tools and prompts, with AI prioritization of every notification
- **Card Workspaces:** Agent workflows for complex tasks (bug fixes, code reviews) — interactive workspaces where you collaborate with a coding agent (Claude Code, Gemini CLI, or Codex) through multiple approval steps
- **Card Research:** Launch on-demand deep research sessions on any card — a coding agent investigates with web search, semantic context, and sandboxed file access
- **Spaces:** User-defined contexts grouping event sources with per-space model and API key configurations
- **Context Association:** Automatically links related cards across platforms using semantic similarity and LLM confirmation. Learns from your corrections to improve grouping accuracy over time
- **Cross-platform memory:** Entity resolution links "BUG-1234" in Jira to "PR-891" in Bitbucket to "the payment bug" in Slack
- **Daily Briefing:** Morning summary of overnight activity, pending cards, and today's calendar with context
- **Analytics Dashboard:** Track events processed, time saved, LLM costs (broken down by feature and pipeline step), and approval rates
- **Budget Tracking:** Monitor LLM costs by feature (Pulse, Omni, Chat, Coherence) with monthly caps and automatic pause when limits are reached
- **Chat sidebar:** Ask Laya questions about your events, projects, and context
- **Coherence:** Cross-platform entity search traces any person, ticket, or PR across all platforms using local vector search (ChromaDB), with AI-generated narratives
- **Egress:** Execute outbound actions (emails, Slack messages, PR comments) directly from Laya with preview-before-send
- **Omni:** Rolling cross-platform summary that answers "where am I right now?" with four temporal layers (Attention, Recent, Period, Milestone) and progressive AI compression
- **Bookmarks:** Pin important cards for quick access regardless of date or status
- **Classification learning:** Laya extracts rules from your priority/persona corrections and improves classification automatically over time
- **Context learning:** Laya extracts grouping rules from your link/unlink corrections and improves context association over time
- **Dead event recovery:** Failed events are tracked with error context and can be manually retried from the audit log
- **Privacy-aware:** Three-tier data classification with cloud/local processing options

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop Shell | Tauri v2 (Rust) |
| Frontend | Svelte 5 (runes) + Skeleton UI + Tailwind CSS v4 |
| Backend | Python 3.10+ / FastAPI / asyncio |
| LLM Interface | LiteLLM (supports Anthropic, OpenAI, Google, Ollama) |
| Integration Gateway | n8n (local Node.js on port 45678) |
| Structured Storage | SQLite (async via aiosqlite, WAL mode) |
| Vector Storage | ChromaDB (embedded PersistentClient) |
| Embeddings | ONNX (built-in to ChromaDB) or sentence-transformers (optional) |
| Coding Agents | Claude Code / Gemini CLI / OpenAI Codex CLI |

## Project Structure

```
laya/
├── engine/                  # Python FastAPI backend
│   ├── laya/
│   │   ├── main.py          # Entry point (uvicorn server on :8420)
│   │   ├── config.py        # Settings, paths, agent detection
│   │   ├── api/             # REST + WebSocket endpoints (23 routers)
│   │   ├── db/              # SQLite + ChromaDB + 46 migrations
│   │   ├── pipeline/        # Event processing (ingest → route → stage → emit → trace → learn → context_learn → omni)
│   │   ├── llm/             # LiteLLM client, prompts, MCP tools
│   │   ├── agents/          # Coding agent adapters (Claude, Gemini, Codex)
│   │   ├── workers/         # Multi-persona LLM workers (engineer, comms, ops)
│   │   ├── egress/          # Outbound action execution (8 platforms)
│   │   ├── integrations/    # n8n bootstrap & client
│   │   └── security/        # OS keychain integration
│   ├── requirements.txt     # Core Python dependencies
│   └── requirements-ml.txt  # Optional: torch + sentence-transformers
│
├── ui/                      # SvelteKit + Tauri desktop app
│   ├── src/                 # Svelte 5 frontend (runes syntax)
│   │   ├── routes/          # Pages (feed, coherence, dashboard, settings, workspace, omni)
│   │   ├── lib/             # Components, API client, stores
│   │   ├── app.css          # Tailwind v4 + theme system
│   │   └── app.html
│   ├── src-tauri/           # Rust/Tauri shell
│   │   ├── src/
│   │   │   ├── lib.rs       # Tauri setup, commands, health polling, tray
│   │   │   ├── sidecar.rs   # Python venv lifecycle & engine spawning
│   │   │   └── n8n.rs       # n8n process management
│   │   ├── tauri.conf.json  # Tauri config (resources, icons, window)
│   │   └── resources/       # Bundled engine source (production builds)
│   ├── package.json
│   └── svelte.config.js     # Static adapter (SPA mode)
│
├── n8n/
│   └── workflows/           # Integration workflows (JSON, ~14 files)
│
├── scripts/
│   ├── setup-dev.sh         # One-time dev environment setup
│   ├── dev.sh               # Start engine + Tauri dev server
│   ├── build.sh             # Production build
│   └── update_icons.sh      # Icon generation
│
├── landing/                 # Landing page
└── docs/                    # Architecture & design documents
```

## Development

### Prerequisites

You need three runtimes installed. Here's how to get each one:

#### Python 3.10+

- **macOS:** `brew install python@3.12` (or download from [python.org](https://www.python.org/downloads/))
- **Ubuntu/Debian:** `sudo apt install python3 python3-venv python3-pip`
- **Windows:** Download from [python.org](https://www.python.org/downloads/) (check "Add to PATH" during install)

Verify: `python3 --version`

#### Node.js 20+

- **All platforms:** Download from [nodejs.org](https://nodejs.org/) (LTS recommended), or use a version manager like [nvm](https://github.com/nvm-sh/nvm) / [fnm](https://github.com/Schniz/fnm)
- **macOS:** `brew install node`
- **Ubuntu/Debian:** `curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs`

Verify: `node --version && npm --version`

#### Rust toolchain

Install via [rustup](https://rustup.rs/):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Verify: `cargo --version`

#### Platform-specific dependencies

**macOS:**

```bash
xcode-select --install
```

**Linux (Ubuntu/Debian):**

Tauri v2 requires system libraries for GTK, WebKit, and app-indicator support:

```bash
sudo apt install -y libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf
```

### Troubleshooting

<details>
<summary><strong>Linux: Tailwind CSS classes missing or styles not updating</strong></summary>

The default Linux inotify file watcher limit (65,536) can be too low for this project -- Vite needs to watch the source files while the Rust `target/` directory consumes most of the quota, causing Tailwind CSS to silently fail to generate utility classes. Increase the limit:

```bash
# Immediate (resets on reboot)
echo 524288 | sudo tee /proc/sys/fs/inotify/max_user_watches

# Permanent
echo 'fs.inotify.max_user_watches=524288' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

</details>

### Setup

```bash
scripts/setup-dev.sh
```

This script does the following:

1. Checks that `python3`, `node`, `npm`, and `cargo` are available
2. Creates a Python virtual environment at `engine/.venv/` and installs dependencies from `engine/requirements.txt`
3. Installs npm packages for the UI (`ui/node_modules/`)
4. Installs n8n as a local npm package into `~/.laya/n8n_module/`
5. Creates data directories at `~/.laya/data/` and `~/.laya/logs/`

### Running Locally

```bash
scripts/dev.sh
```

This starts two processes:

1. **Python engine** -- `python -m laya.main` (with hot reload) at http://127.0.0.1:8420
2. **Tauri dev server** -- `npx @tauri-apps/cli dev` which starts Vite at http://localhost:5173 and opens the Tauri window

n8n is managed automatically by the Tauri app -- it starts on launch (port 45678) and stops on quit.

> **Note:** If the engine fails with "Address already in use", a stale engine process may be holding port 8420. The engine will attempt to kill it automatically on startup.

### Configuration

On first launch, the engine creates config files in `~/.laya/`:

| File | Purpose |
|------|---------|
| `settings.json` | Models, agent paths, privacy settings, pipeline params |
| `team.json` | Team member context |
| `rules.json` | Event filtering rules |
| `repos.json` | Git repository paths and metadata |

API keys (Anthropic, OpenAI, Google, etc.) are stored securely in your OS keychain and can be configured through the Settings UI.

### Custom Prompts

Laya's AI pipeline uses system prompts at every stage (routing, staging, summarization, chat, etc.). All prompts ship with sensible defaults, but you can override any of them by placing files in `~/.laya/prompts/`:

```bash
mkdir -p ~/.laya/prompts

# Override the router prompt (controls event classification)
vim ~/.laya/prompts/router.md

# Override a worker persona
vim ~/.laya/prompts/engineer.md

# Reload without restarting
curl -X POST http://127.0.0.1:8420/prompts/reload
```

Available prompt files: `router.md`, `stager.md`, `omni.md`, `group_summary_initial.md`, `group_summary_rolling.md`, `briefing.md`, `summarizer.md`, `summarizer_status_change.md`, `engineer.md`, `comms.md`, `sales.md`, `hr.md`, `ops.md`, `finance.md`, `chat.md`, `chat_title.md`, `chat_polish.md`, `learner.md`, `context_learner.md`, `trace_narrative.md`, `trace_summary.md`, `trace_filter.md`.

Custom prompts fully replace the built-in default for that stage. If a file is deleted, the hardcoded default is used automatically. The engine never creates or modifies files in this directory. Use `GET /prompts` to check which prompts are currently overridden.

### Data Storage

| Store | Location | Purpose |
|-------|----------|---------|
| SQLite | `~/.laya/data/laya.db` | Events, cards, workspaces, spaces, traces, egress, chat |
| ChromaDB | `~/.laya/data/chroma/` | Vector embeddings for semantic search |
| n8n | `~/.laya/n8n/` | Workflow data, credentials (encrypted) |
| Logs | `~/.laya/logs/engine.log` | Rotating engine logs (10 MB x 5 files) |

## Building for Distribution

Laya bundles the Python engine source into the Tauri app. On first launch, the app creates a Python virtual environment at `~/.laya/venv/` and installs dependencies automatically -- no Python installation is required on the end user's machine beyond what the app manages.

### Build Command

```bash
scripts/build.sh
```

This does two things:

1. **Bundles engine source** -- copies `engine/laya/`, `requirements.txt`, `requirements-ml.txt`, and `n8n/workflows/` into `ui/src-tauri/resources/engine/`
2. **Builds the Tauri app** -- compiles the Rust shell, bundles the SvelteKit frontend, and packages everything into a platform-native installer

### Build Options

```bash
scripts/build.sh                                   # Build for current platform
scripts/build.sh --target x86_64-apple-darwin      # Cross-compile for Intel Mac
scripts/build.sh --universal                       # Universal binary (arm64 + x86_64)
scripts/build.sh --sign "Developer ID App: ..."    # macOS code signing
scripts/build.sh --skip-engine                     # Skip engine bundling (reuse previous)
```

### Build Output

| Platform | Format | Path |
|----------|--------|------|
| macOS | `.app` | `ui/src-tauri/target/release/bundle/macos/Laya.app` |
| macOS | `.dmg` | `ui/src-tauri/target/release/bundle/dmg/Laya_0.1.0_<arch>.dmg` |
| Windows | `.msi` | `ui/src-tauri/target/release/bundle/msi/` |
| Windows | `.exe` | `ui/src-tauri/target/release/bundle/nsis/` |
| Linux | `.deb` | `ui/src-tauri/target/release/bundle/deb/` |
| Linux | AppImage | `ui/src-tauri/target/release/bundle/appimage/` |

> **Note:** macOS builds are unsigned by default. Unsigned apps trigger Gatekeeper -- users must right-click > Open to bypass. Pass `--sign` with an Apple Developer identity to produce a signed build.

## Documentation

Architecture and design documents in [`docs/`](./docs/):

- [**System Architecture**](./docs/architecture.md) -- Component diagrams and service descriptions
- [**Event Schema**](./docs/event-schema.md) -- The Laya Event schema specification
- [**API Contracts**](./docs/api-contracts.md) -- REST, WebSocket, and inter-service API definitions
- [**Database Schema**](./docs/database-schema.md) -- SQLite tables and ChromaDB collection design
- [**Project Structure**](./docs/project-structure.md) -- Repository layout and config file schemas
- [**Implementation Plan**](./docs/implementation-plan.md) -- Milestone breakdown
- [**Decision Log**](./docs/decision-log.md) -- Architectural decisions with rationale
