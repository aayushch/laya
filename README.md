# Laya: The AI Operating System

**A cadence for professional orchestration.**

Laya is a local-first desktop application that acts as an AI operating system for professionals. It intercepts events from your professional tools (Jira, Bitbucket, Slack, Gmail, Calendar), performs autonomous research and action-staging using LLM-powered agents, and presents you with ready-to-approve **Action Cards** -- so the answer is ready before you open the notification.

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

## Key Features (v0.1)

- **Multi-persona brain:** Routes events to specialized AI personas (Engineer, Comms, Ops) with domain-specific tools and prompts
- **Card Workspaces:** Complex tasks (bug fixes, code reviews) open interactive workspaces where you collaborate with a coding agent (Claude Code, Gemini CLI, or Codex) through multiple approval steps
- **Cross-platform memory:** Entity resolution links "BUG-1234" in Jira to "PR-891" in Bitbucket to "the payment bug" in Slack
- **Daily Briefing:** Morning summary of overnight activity, pending cards, and today's calendar with context
- **Analytics Dashboard:** Track events processed, time saved, LLM costs, and approval rates
- **Chat sidebar:** Ask Laya questions about your events, projects, and context
- **Learning loop:** Laya improves its classification and priority based on your approval/dismiss patterns
- **Privacy-aware:** Three-tier data classification with cloud/local processing options

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop Shell | Tauri v2 (Rust) |
| Frontend | Svelte 5 + Skeleton UI + Tailwind CSS |
| Backend | Python 3.10+ / FastAPI / LangGraph |
| LLM Interface | LiteLLM (supports Anthropic, OpenAI, Google, Ollama) |
| Integration Gateway | n8n (local npm) |
| Structured Storage | SQLite |
| Vector Storage | ChromaDB (embedded) |
| Embeddings | nomic-embed / all-MiniLM (local via sentence-transformers) |
| Coding Agents | Claude Code / Gemini CLI / OpenAI Codex CLI |

## Documentation

All design and architecture documents are in [`docs/`](./docs/):

- [**System Architecture**](./docs/architecture.md) -- Complete architecture with diagrams and component descriptions
- [**Event Schema**](./docs/event-schema.md) -- The Laya Event schema specification (inbound + outbound)
- [**API Contracts**](./docs/api-contracts.md) -- REST, WebSocket, and inter-service API definitions
- [**Database Schema**](./docs/database-schema.md) -- SQLite tables and ChromaDB collection design
- [**Project Structure**](./docs/project-structure.md) -- Repository layout, directory structure, config file schemas
- [**Implementation Plan**](./docs/implementation-plan.md) -- 8 milestones across ~19 weeks
- [**Decision Log**](./docs/decision-log.md) -- All 57 architectural decisions with rationale

## Concept Materials

The original concept documents that informed this architecture:

- [Laya Architecture Spec.md](./Laya%20Architecture%20Spec.md) -- Initial technical architecture vision
- [Laya Master Orchestrator.md](./Laya%20Master%20Orchestrator.md) -- Multi-persona system prompt design
- [Laya - The Professional AI Operating System.pdf](./Laya%20-%20The%20Professional%20AI%20Operating%20System.pdf) -- Concept slide deck

## Development

### Prerequisites

- Python 3.10+
- Node.js 20.19+ (required by `@sveltejs/vite-plugin-svelte`)
- Rust toolchain (via `rustup`)

#### Platform-specific dependencies

**macOS:**

```bash
xcode-select --install
```

**Linux (Ubuntu/Debian):**

Tauri v2 requires several system libraries for GTK, WebKit, and app-indicator support:

```bash
sudo apt install -y libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf
```

The default Linux inotify file watcher limit (65,536) is too low for this project -- Vite needs to watch the source files while the Rust `target/` directory consumes most of the quota, causing Tailwind CSS to silently fail to generate utility classes. Increase the limit:

```bash
# Immediate (resets on reboot)
echo 524288 | sudo tee /proc/sys/fs/inotify/max_user_watches

# Permanent
echo 'fs.inotify.max_user_watches=524288' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Setup

```bash
scripts/setup-dev.sh   # One-time: creates venv, installs deps, installs n8n via npm
```

### Running Locally

```bash
scripts/dev.sh
# Starts Python engine and Tauri dev window
# Engine: http://127.0.0.1:8420  |  UI: http://localhost:5173
```

> **Note:** If the engine fails with "Address already in use", a stale engine process may be holding port 8420. The engine will attempt to kill it automatically on startup.

## Building the Installable App

Laya is a Tauri desktop app. The build produces platform-native installers.

### Quick Build (Development Machine)

This builds the app using your local Python venv -- the resulting app expects Python and dependencies to be installed on the target machine.

```bash
cd ui
npm run build          # Build SvelteKit frontend -> ui/build/
npx @tauri-apps/cli build        # Compile Rust + bundle into platform installer
```

Output locations:

| Platform | Format | Path |
|----------|--------|------|
| macOS | `.app` | `ui/src-tauri/target/release/bundle/macos/Laya.app` |
| macOS | `.dmg` | `ui/src-tauri/target/release/bundle/dmg/Laya_0.1.0_<arch>.dmg` |
| Windows | `.msi` | `ui/src-tauri/target/release/bundle/msi/` |
| Windows | `.exe` | `ui/src-tauri/target/release/bundle/nsis/` |
| Linux | `.deb` | `ui/src-tauri/target/release/bundle/deb/` |
| Linux | AppImage | `ui/src-tauri/target/release/bundle/appimage/` |

> **Note:** macOS builds will be unsigned unless you configure an Apple Developer certificate. Unsigned apps trigger Gatekeeper -- users must right-click > Open to bypass.

### Self-Contained Build (Bundled Python Engine)

For a fully distributable app that doesn't require Python on the user's machine, the Python engine must be compiled into a standalone binary using **PyInstaller** and included as a Tauri sidecar.

#### Why PyInstaller

| Tool | Status |
|------|--------|
| **PyInstaller** | Recommended -- most mature, proven with FastAPI/uvicorn/chromadb, existing Tauri v2 sidecar examples |
| Nuitka | Known issues with FastAPI/uvicorn multi-worker mode |
| PyOxidizer | Abandoned since 2023 |
| cx_Freeze | Viable but less community coverage for this dependency stack |

#### Step 1: Reduce dependency footprint

The engine currently uses `sentence-transformers` + `torch` (~600MB+) for ChromaDB embeddings. ChromaDB ships a built-in ONNX embedding function using the same `all-MiniLM-L6-v2` model via `onnxruntime` (~15MB). Switching to the built-in function and removing `torch` / `sentence-transformers` / `transformers` / `scipy` / `scikit-learn` from `requirements.txt` cuts the bundle from ~2GB to ~200MB.

#### Step 2: Build the engine binary

```bash
cd engine
source .venv/bin/activate
pip install pyinstaller

# Build standalone binary (output: ui/src-tauri/binaries/)
pyinstaller laya-engine.spec --distpath ../ui/src-tauri/binaries --clean -y

# Rename with target triple (required by Tauri)
TARGET=$(rustc --print host-tuple)
mv ../ui/src-tauri/binaries/laya-engine/laya-engine \
   ../ui/src-tauri/binaries/laya-engine-$TARGET
```

The PyInstaller spec file (`engine/laya-engine.spec`) must include hidden imports for chromadb, uvicorn, litellm, and data files for SQLite migrations.

#### Step 3: Configure Tauri sidecar

In `ui/src-tauri/tauri.conf.json`, add `externalBin`:

```json
{
  "bundle": {
    "externalBin": ["binaries/laya-engine"]
  }
}
```

Tauri resolves the platform-specific binary by appending the target triple. Place binaries as:

```
ui/src-tauri/binaries/
  laya-engine-aarch64-apple-darwin        # macOS Apple Silicon
  laya-engine-x86_64-apple-darwin         # macOS Intel
  laya-engine-x86_64-unknown-linux-gnu    # Linux x64
  laya-engine-x86_64-pc-windows-msvc.exe  # Windows x64
```

#### Step 4: Build the full app

```bash
cd ui
npx @tauri-apps/cli build    # Bundles SvelteKit + Rust + sidecar binary into installer
```

#### Estimated bundle sizes

| Configuration | Binary Size |
|---------------|-------------|
| With torch (current) | ~1.5--2.5 GB |
| Without torch, ONNX embeddings (recommended) | ~150--300 MB |

#### CI/CD

For cross-platform releases, use GitHub Actions with a matrix build:

1. Run PyInstaller on each target OS (macOS arm64, macOS x64, Linux x64, Windows x64)
2. Feed the resulting binaries into the Tauri build step
3. Tauri produces the platform-specific installer

> **Development workflow** is unaffected -- `scripts/dev.sh` and `npx @tauri-apps/cli dev` continue to use the Python venv directly. The bundled binary is only used for production builds.

## Project Status

**Phase:** Pre-development (architecture and design complete)

**Next step:** Milestone 1 -- Skeleton (get Tauri + Python engine + n8n running and talking to each other)
