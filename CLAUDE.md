# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Laya

Laya is a local-first desktop app (Tauri + Svelte + Python) that intercepts professional tool events (Jira, Slack, Gmail, Bitbucket, Calendar), classifies them with LLM-powered personas (Engineer, Comms, Ops), stages actions, and presents Action Cards for user approval. n8n handles event ingestion and outbound action execution.

## Development Commands

```bash
# One-time setup (creates venvs, installs deps)
scripts/setup-dev.sh

# Run dev environment (Tauri dev server manages engine + n8n)
scripts/dev.sh

# Run engine standalone (for backend-only work)
cd engine && source .venv/bin/activate && python -m laya.main

# Run tests
cd engine && source .venv/bin/activate && pytest

# Run a single test file
cd engine && source .venv/bin/activate && pytest tests/test_cards_api.py

# Run a single test
cd engine && source .venv/bin/activate && pytest tests/test_cards_api.py::test_function_name -v

# Frontend dev only (no Tauri shell)
cd ui && npm run dev

# Type check frontend
cd ui && npx svelte-check

# Production build
scripts/build.sh
```

## Architecture

```
Event Sources → n8n (port 45678) → Engine (port 8420) → UI (Tauri + Svelte, port 5173)
                                         ↓
                               SQLite + ChromaDB (~/.laya/data/)
```

**Three layers:**
- **Engine** (`engine/laya/`): Python FastAPI backend. Pipeline processes events through `ingest → router → stager → emit → context_assoc → trace → learn → context_learn → omni`. LLM calls go through LiteLLM (`llm/client.py`). 23 API routers in `api/`. Async throughout (aiosqlite, httpx).
- **UI** (`ui/src/`): SvelteKit frontend using Svelte 5 runes (`$state`, `$derived`, `$effect`, `$props`). Skeleton UI v4 + Tailwind CSS v4. Static adapter (SPA mode). Key routes: feed, coherence, dashboard, settings, workspace, omni.
- **Tauri Shell** (`ui/src-tauri/`): Rust process that manages engine and n8n lifecycle (`sidecar.rs`, `n8n.rs`), tray icon, and native APIs.

**Pipeline flow** (`engine/laya/pipeline/`):
1. `ingest.py` — receives normalized events from n8n webhooks
2. `router.py` — classifies event → persona (Engineer/Comms/Ops) with priority
3. `stager.py` — persona worker stages actions via LLM
4. `emit.py` — creates Action Cards in SQLite
5. `trace.py` — indexes into ChromaDB for semantic search
6. `learn.py` — extracts classification rules from user feedback
7. `omni.py` — rolling cross-platform summary with progressive summarization

**Egress** (`engine/laya/egress/`): Outbound action execution across 8 platforms (GitHub, Bitbucket, Jira, Linear, Gmail, Slack, Calendar, Outlook). OAuth connections managed via `connections.py` + OS keychain.

**Spaces**: User-defined contexts grouping event sources. `space_id` threads through the entire pipeline. Default space has `space_id='default'`.

## Key Conventions

- **Svelte 5 runes only**: Use `$state`, `$derived`, `$effect` — never `$:` reactive declarations
- **Async everywhere in engine**: All DB access, HTTP, and pipeline functions are async
- **SQLite migrations**: Numbered files in `engine/laya/db/migrations/` (001-046). New migrations get the next number. Migration runner in `db/migrate.py` applies on startup.
- **LLM prompts**: Organized by role in `engine/laya/llm/prompts/` (router, stager, engineer, comms, ops, chat, etc.)
- **Config files**: User settings live in `~/.laya/` (settings.json, team.json, rules.json, repos.json). API keys stored in OS keychain via `security/keychain.py`.
- **Test fixtures**: `engine/tests/conftest.py` provides `db` (in-memory SQLite with all migrations), `sample_event`, `bot_event`, `slack_event`, `sample_team`. All test fixtures are async (`@pytest_asyncio.fixture`).
- **Theme system**: CSS custom properties in `ui/src/app.css` with `--color-laya-*` brand tokens. Dark/light mode via `data-theme` attribute on `<html>`.
- **Feed layout**: 3-column flex layout with round-robin card distribution (not CSS columns — intentional, see memory for rationale).
- **Comment workarounds and defensive fixes**: Any time a workaround, defensive fix, or non-obvious hack is applied, leave a comment in the code explaining *why* the fix exists and what breaks without it. Future readers (and Claude Code) should understand the reasoning without having to rediscover the problem.
- **n8n workflow versions**: Every time a bundled workflow JSON in `n8n/workflows/` is modified, bump its `meta.laya_version` field. The engine's startup sync compares this version against deployed records and propagates updates to cloned workflow instances only when the version changes.

## Ports

| Service | Port |
|---------|------|
| Engine (FastAPI) | 8420 |
| UI (Vite dev) | 5173 |
| n8n | 45678 |

## Dependencies

- **Python**: `engine/requirements.txt` (core), `engine/requirements-ml.txt` (optional: torch + sentence-transformers)
- **Frontend**: `ui/package.json` — Svelte 5, SvelteKit, Skeleton UI, Tailwind v4, Tauri APIs
- **Rust**: `ui/src-tauri/Cargo.toml` — Tauri v2, tokio, reqwest

## Pre-existing Issues

Some TypeScript errors exist in `+layout.svelte`, `dashboard/+page.svelte`, `setup/+page.svelte`, and `workspace/[card_id]/+page.svelte` — these are pre-existing and unrelated to feed work.
