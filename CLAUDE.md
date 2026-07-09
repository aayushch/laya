# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Laya

Laya is a local-first desktop app (Tauri + Svelte + Python) that intercepts professional tool events (Jira, Slack, Gmail, GitHub, Bitbucket, Linear, Notion, Google Calendar, Outlook Calendar, Outlook Email), classifies them with LLM-powered personas (Engineer, Comms, Ops, Sales, HR, Finance), stages actions, and presents Action Cards for user approval. n8n handles event ingestion and outbound action execution.

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

# Frontend unit tests (vitest — pure-logic modules like the feed reducer/flip utils)
cd ui && npm test

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
- **Engine** (`engine/laya/`): Python FastAPI backend. Pipeline processes events through `ingest → space_resolution → rules → router → workers → stager → emit` with post-emit async steps (see below). LLM calls go through LiteLLM (`llm/client.py`), which can also drive installed CLI coding agents (Claude Code, Codex, Gemini, Pi) as inference backends via `llm/agent_backend.py` (model-id form `agent/<id>/<model>`). ~27 API routers in `api/` — the large card surface is split into `cards_common` (shared row→model helpers) + route modules (`cards_feed`/`cards_lifecycle`/`cards_readstate`/`cards_payload`/`cards_agent`/`cards_groups`), aggregated in-place via `include_router` into one `cards_router` (order matters: `/cards/grouped` before `/cards/{card_id}`; P7-6). Pydantic models in `models/`. Async throughout (aiosqlite, httpx).
- **UI** (`ui/src/`): SvelteKit frontend using Svelte 5 runes (`$state`, `$derived`, `$effect`, `$props`). Skeleton UI v4 + Tailwind CSS v4. Static adapter (SPA mode). Key routes: feed, coherence, dashboard, settings, workspace, omni, setup, status, legal.
- **Tauri Shell** (`ui/src-tauri/`): Rust process that manages engine and n8n lifecycle (`sidecar.rs`, `n8n.rs`), tray icon, and native APIs.

**Pipeline flow** (`engine/laya/pipeline/`):

Main pipeline (`queue.py` orchestrates):
1. `ingest.py` — receives normalized events from n8n webhooks
2. `space_resolution.py` — resolves event to a space
3. `rules.py` — applies user-defined filter/routing rules
4. `router.py` — classifies event → persona with priority
5. `workers.py` — dispatches persona workers (Engineer/Comms/Ops/Sales/HR/Finance)
6. `stager.py` — stages actions via LLM
7. `emit.py` — creates Action Cards in SQLite

Post-emit (triggered by `emit.py`):
- `entity_resolution.py` — resolves semantic entities across platforms
- `context_grouping.py` — groups related cards into context groups
- `trace.py` — indexes into ChromaDB for semantic search
- `tags.py` — persists stager-suggested tags
- `group_summary.py` — rolling LLM summary per entity group
- `omni.py` — rolling cross-platform summary with progressive summarization

Supporting pipelines (triggered separately):
- `chat.py` — chat processing pipeline (hybrid retrieval, RRF, context packing, tool loop)
- `executor.py` — action execution service, delegates to egress and manages card lifecycle
- `context_presets.py` — context association strictness presets
- `learn.py` — extracts classification rules (priority/persona) from user corrections; `context_learn.py` — extracts **context rules** (natural-language grouping directives, distinct from classification rules) from link/unlink corrections. BOTH LLM-consolidate their learned rules once they exceed a threshold (capping unbounded router/grouping-prompt growth) and share their fetch/mark-processed/space-scan query helpers via `pipeline/learn_common.py`. Learned and manual context rules are managed via `api/context_rules_api.py` and injected into the context-grouping prompt
- `processing_rules.py` — applies automated processing rules, logging every firing to `processing_rule_firings`
- `briefing.py` — generates daily briefings
- `summarize.py` — daily summary generation
- `feedback.py` / `budget.py` — feedback processing and monthly $ token-budget tracking
- `agent_budget.py` — window-based usage-limit budgeting for agent inference backends (auto-pause ingestion when an agent's rolling window is exhausted, auto-resume at window reset)

**Egress** (`engine/laya/egress/`): Outbound action execution across 9 platforms (GitHub, Bitbucket, Jira, Linear, Gmail, Slack, Calendar, Outlook, Notion). Each platform owns its contract (capabilities, draft schema, terminal events, normalize/validate) as a `Platform` subclass in `platforms/` extending `platforms/base.py`; `registry.py` is a thin facade that delegates to these adapters (adding a platform = write one file, not edit many tables). Execution backends in `backends/` (n8n, SMTP — `platforms/smtp.py` is a data-only adapter for the SMTP backend), connection management via `connections.py` + `oauth.py` + OS keychain, action routing via `router.py` + `registry.py`, and chat-driven egress via `tools.py` + `tool_handlers.py`.

**Agents** (`engine/laya/agents/`): CLI coding agent adapters (Claude Code, Gemini CLI, Codex CLI, Pi CLI) for the workspace feature. Abstract protocol in `base.py`, session management in `session_manager.py`.

**MCP** (`engine/laya/mcp/`): HTTP/SSE MCP server exposing Laya tools to external agents. Scope-filtered per user settings, space-aware via context var.

**Chat tools** (`engine/laya/llm/tools/`): Tool definitions for the chat pipeline — card, contact, entity, event, search, settings, summary, and rule-management tools (`rules_tools.py` lets chat list/create/update/delete filter, classification, and processing rules; changes are audited and broadcast over WebSocket).

**Persona workers** (`engine/laya/workers/`): Spec-driven — `workers/persona.py` holds `PERSONA_SPECS` + one `run_persona_worker` for the five drafting personas (Comms, Ops, Sales, HR, Finance); Engineer keeps its own module (`engineer.py`) for the agent-prompt path. Shared base class in `base.py` (P7-2).

**Spaces**: User-defined contexts grouping event sources. `space_id` threads through the entire pipeline. Default space has `space_id='default'`.

## Key Conventions

- **Svelte 5 runes only**: Use `$state`, `$derived`, `$effect` — never `$:` reactive declarations
- **Async everywhere in engine**: All DB access, HTTP, and pipeline functions are async
- **SQLite migrations**: Numbered files in `engine/laya/db/migrations/` (001-071). New migrations get the next number. Migration runner in `db/migrate.py` applies on startup.
- **Multi-statement invariants**: Laya uses ONE shared aiosqlite connection (no per-request isolation). For a sequence of writes that must land atomically, wrap them in the `db/sqlite.transaction()` async context manager (a module `asyncio.Lock` + commit-on-success / rollback-on-failure). It's applied to the low-frequency off-hot-path invariants (space delete, card cascade delete, context-group merge/unlink); deliberately NOT the hot `_persist_card` emit path.
- **Hybrid search / FTS5**: Chat and trace retrieval combine vector search (ChromaDB) with lexical BM25 search over SQLite FTS5 virtual tables (`cards_fts`, `events_fts`), merged via Reciprocal Rank Fusion. FTS tables are built at startup and kept in sync by SQL triggers (`db/fts.py`); retrieval degrades to `LIKE` if the SQLite build lacks FTS5. The `action_cards.thread_context` column (a short thread blurb persisted at emit time) is indexed so terse follow-up cards ("Approved.") stay findable by their thread's keywords ("Contextual BM25"). Shared retrieval primitives live in `laya/retrieval.py` — `extract_keywords` (+ `STOPWORDS`), `reciprocal_rank_fusion`, and `fts_or_like` (the try-FTS-then-fall-back-to-LIKE dispatch). The chat/trace/card-search stacks all call these; don't re-implement per-stack (they had drifted — P7-1).
- **LLM prompts**: Organized by role in `engine/laya/llm/prompts/` (router, stager, engineer, comms, ops, sales, hr, finance, chat, omni, briefing, group_summary, research, overrides, trace_filter, context_learner, context_rule_consolidator, classification_rule_consolidator, etc.). **Gate conditional content by platform/intent instead of always-including it** (token cost matters on small local windows): `build_router_system_prompt(platforms)` / `build_stager_system_prompt(platform)` assemble only the relevant platform lifecycle blocks, and `llm/tools/definitions.select_chat_tools()` ships only the keyword-relevant chat tool groups. Follow this when adding new conditional blocks (P6).
- **Agent inference backends**: `llm_call()` accepts model ids of the form `agent/<agent_id>/<model>` (agent_id ∈ claude_code, codex_cli, gemini_cli, pi_cli) to run a stage on an installed CLI agent's own quota. Claude Code is the "native" tier (schema enforced via `--json-schema`); others are "best-effort" (schema injected as text + parse/validate/retry). Agents run headless in an ephemeral cwd with tools denied; usage is metered against window-based limits by `agent_budget.py`.
- **Config files**: User settings live in `~/.laya/` (settings.json, team.json, rules.json, repos.json). API keys stored in OS keychain via `security/keychain.py`.
- **Test fixtures**: `engine/tests/conftest.py` provides `db` (in-memory SQLite with all migrations), `sample_event`, `bot_event`, `slack_event`, `sample_team`. All test fixtures are async (`@pytest_asyncio.fixture`).
- **Theme system**: CSS custom properties in `ui/src/app.css` with `--color-laya-*` brand tokens. Dark/light mode via `data-theme` attribute on `<html>`.
- **Feed layout**: 3-column flex layout with round-robin card distribution (not CSS columns — intentional, see memory for rationale). `feed/+page.svelte` is decomposed (P7-7): the `card_updated` WS reducer → pure, unit-tested `lib/feed/cardUpdateReducer.ts`; the FLIP animation → `lib/utils/flip.ts` (`capturePositions`/`playFlip`); and the SummaryModal / FilterPopover / RecentDrawer panels → `lib/components/feed/`. Prefer extending those over re-growing the page.
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

## Other Directories

- **Landing** (`landing/`): Public website (static HTML/CSS) with llms.txt, privacy, terms, sitemap.
- **Marketing** (`marketing/`): Launch strategy docs (Product Hunt, HN, blog posts, SEO, social).
- **Docs** (`engine/docs/`): Architecture design docs (egress, chat, pipeline lifecycle, processing rules).
