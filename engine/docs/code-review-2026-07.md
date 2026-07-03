# Laya — Full-Product Code Review (July 2026)

**Date:** 2026-07-02
**Method:** Nine parallel deep reviews covering the core pipeline, post-emit steps, supporting pipelines/scheduler, the LLM layer and prompts, chat/retrieval, egress, the API/DB layer, the Svelte UI, and the agents/MCP/Tauri shell. The four most consequential single-source claims were re-verified directly against source before inclusion. No code was changed as part of this review.

**Overall verdict:** the architecture is fundamentally sound and several hard problems are solved well — the durable DB-backed event queue with crash recovery, the zero-LLM-inline omni queue, debounced/batched summarization, the `Platform` adapter pattern in egress, the single `llm_call()` seam with local-model salvage layers, and correctly-escaped FTS5 hybrid retrieval. The problems cluster in four places: **(1)** a set of real bugs, several of which silently waste LLM calls or corrupt data; **(2)** event-loop blocking (sync embedding, keychain, file IO) that freezes the whole app during bursts; **(3)** ~8–10K tokens of avoidable fixed overhead per chat turn and ~1.5K per pipeline event; **(4)** ~1,800 lines of removable duplication that has already caused behavioral drift.

---

## 1. Highest-impact bugs (verified in source)

**1.1 — The stager's `context_match` output is discarded, wasting one LLM call per grouped card.** The stager schema *requires* `context_match` (`llm/prompts/stager.py:667,692` — the model pays output tokens for it on every event), but `_parse_stager_response` never carries it into `ActionCardData` (which has no such field), so `getattr(stager_output, "context_match", None)` at `emit.py:426` is always `None`. Every emit falls into the fallback ChromaDB search + `confirm_context_link` LLM call — the exact call this path was designed to avoid — and the `hard_gate` validation at `emit.py:432-467` never executes. Fix: add `context_match: dict | None` to `ActionCardData` and thread it through. Zero token cost, removes a whole call class.

**1.2 — Deleted cards' embeddings are never removed from ChromaDB; ghost cards pollute chat forever.** Manual delete calls `delete_document(f"card_{card_id}")` (`cards_api.py:1035`) but the doc was indexed with `doc_id=card_id`, which is *already* `card_<hex>` (`emit.py:386,624`) — the delete targets `card_card_…` and silently removes nothing. Retention housekeeping (`scheduler.py:34-62`) never touches ChromaDB at all. Since `_semantic_search` returns raw ChromaDB documents without SQLite hydration, deleted content keeps re-entering chat prompts indefinitely, and the vector store grows forever — a retention-promise violation for a local-first app.

**1.3 — One hung LLM call wedges the entire pipeline.** `_consumer_loop` (`queue.py:811`) awaits `asyncio.wait(..., ALL_COMPLETED)` and the stale-event reaper only runs at the top of that loop — so a request that ignores its httpx timeout (the documented LMStudio-killed-mid-request case) blocks all processing *and* the mechanism meant to recover from it. When the hang resolves, the reaper has flipped the event to `retrying`, a second task claims it, and the emit-time existing-card check can race → duplicate cards and double LLM spend. Run the reaper as an independent task, have it cancel the matching in-flight task, and bound the batch wait.

**1.4 — `PUT /processing-rules/reorder` is unreachable.** It is declared at `processing_rules_api.py:458`, *after* `PUT /processing-rules/{rule_id}` at line 367; Starlette matches in declaration order, so `reorder` hits the `{rule_id}` route and 422s. Drag-to-reorder in the UI is silently broken. (The file even documents this trap for its GET routes at lines 247-250.)

**1.5 — n8n owner account uses a hardcoded, repo-public password.** `n8n_bootstrap.py:829` uses `_DEFAULT_PASSWORD = "LayaAutoAdmin2026!"` (line 25); `_generate_password()` is dead code. n8n holds OAuth tokens for every connected platform and can execute arbitrary code via its nodes; any local process (or DNS-rebinding page — n8n doesn't validate Host) can log in. One-line fix: `stored_password or _generate_password()` — the code already persists whatever password it used to the keychain.

**1.6 — Phantom budget dollars can pause all ingestion for local-model users.** Unknown models fall to `_DEFAULT_PRICING` ($1/$3 per Mtok) — and that includes every Ollama/LMStudio/custom model *and* `agent/…` backends (which are also separately governed by `agent_budget.py`). A user on free local models accrues fake cost until `pause_for_budget()` deactivates every ingestion workflow. Price unrecognized local/agent models at $0 and exclude `agent/%` rows from the dollar aggregate.

**1.7 — Batch routing silently drops the learning loop.** `build_batch_router_messages` accepts neither `format_feedback_section()` (manual + learned classification rules, corrections) nor memory context, while the single-event path injects both (`pipeline/router.py:100-116`). Batch mode is on by default for cloud routers, so under any burst the user's rules simply don't apply — corrections appear "not to stick" nondeterministically. The batch also matches classifications by array index (misalignment silently misclassifies everything after a gap before the circuit breaker trips) and uses `events_data[0]`'s space for the whole batch. Fix: pass the feedback section once per batch (shared, so cheaper than N single calls), add an echoed `event_index` to the schema, group batches by space.

**1.8 — Scheduler daily jobs silently skip.** Triggers require exact `hour == target and minute == target` (`scheduler.py:281,299-303`), but briefing/omni/learn work is awaited *inline* in the same 60s loop — on a local model, the tick that should match 07:00 lands at 07:02 and the briefing is skipped for the whole day. Change to `>= target` plus the existing date guard, and spawn the work as tasks. Relatedly, the startup month-rollover for budget snapshots is dead code (`main.py:265-289` — both branches `pass`), so a desktop app not running at midnight on the 1st permanently loses monthly history.

**1.9 — Entity-workspace resume invariant is broken on the manual path.** `run_entity_agent` (`cards_api.py:2211`) calls `get_session_for_entity` *without* `include_terminal=True` and only resumes `paused` sessions; since completed sessions land as `completed` (there is no `awaiting_input` session status), the manual "Run Agent" button spawns a duplicate workspace after the first run — orphaning the real one. The processing-rule path (`processing_rules.py:411`) has the correct guard and claims to "mirror the manual flow"; it doesn't. Port that logic over, and add a per-entity lock (both paths are unlocked check-then-act).

**1.10 — CLI agent sessions can deadlock permanently on large output lines.** `subprocess_helper.py:61` spawns without `limit=`, so `readline()` raises on any stream-json line over 64KiB (routine for a Claude Code `Write` tool_use carrying a full file). The generic `except: break` silently ends the reader, then the child blocks writing into the undrained pipe while the engine awaits `process.wait()` — card stuck in `agent_running` forever. Also: the 300s idle timeout SIGKILLs legitimately paused sessions (pause = SIGSTOP = no output) and long silent tool runs. Pass a 10MB limit, suspend the idle clock while paused.

---

## 2. Other functional bugs worth fixing

### Pipeline & post-emit

- **Transient LLM outages become permanent junk cards**: stager exceptions are swallowed into `_build_fallback_card` and the event is marked completed (`stager.py:102-104`) — a 30-second LMStudio restart converts every in-flight event into a degraded card with no actions, while router failures correctly retry. Let transport errors propagate; reserve the fallback for parse failures.
- **Retries re-pay the router**: `run_router` persists `router_output` on the event row, but retries only check the in-memory batch cache (`queue.py:264-273`) — each of up to 3 retries re-runs the router call. Reuse the persisted output on `processing_attempts > 1`. Pure LLM-call savings.
- **Debounce cancel kills in-flight summary runs and drops cards permanently**: `trigger_group_summary_update` (`group_summary.py:62-64`) cancels the task even after it has popped its batch and is mid-LLM-call; `CancelledError` isn't caught, the popped card_ids are never written, and rolling summaries are forward-only — those cards are gone from the summary. Likely whenever local-model latency exceeds the 15s debounce. Same pattern in `summarize.py:77-83`, which *also* has a lost-update race when a 90s flush overlaps a still-running fold (needs a per-space lock).
- **Empty `subject.id` collapses unrelated events into one entity** (`models/event.py:64-70`): null id coerces to `""`, so all such events share `entity_id="gmail:email_thread:"` — false carry-forward, unrelated cards auto-resolved as siblings, merged summaries. Fall back to `event_id` for degenerate events.
- **`entities` table grows unbounded duplicates**: `_create_semantic_link` (`entity_resolution.py:97-117`) INSERTs a fresh-UUID row per recurrence of the same `A <-> B` pair (no unique constraint) — hundreds of junk rows/week that degrade trace's LIKE scans. Meanwhile `confirm_entity_link` (Layer 3) is dead code with a maintained prompt.
- **Trace seeds stuff a refs-CSV into `entity_id`** (`trace.py:577-585`), breaking dedup and feedback exclusion for semantic seeds; the real `entity_id` is available in ChromaDB metadata at embed time.
- **Learned classification rules grow router prompts unboundedly**: no injection cap and no consolidation pass (`feedback.py:77-92`, `learn.py:152-158`), while the context-rules side has both (`max_injection=20` + consolidation at 40). This is the small-local-model failure mode. Port both protections.
- **Briefing**: the duplicate-day check runs *after* the LLM call (`briefing.py:128-165` — reorder it, free savings), and "today's calendar events" uses the UTC day boundary (`briefing.py:96-102`) so UTC+ users get yesterday's meetings — the documented timezone invariant class.
- **Stager per-action parse uses bare key access** (`stager.py:175-183`): one malformed action (likely on best-effort agent backends) raises KeyError, burning 3 full pipeline retries before dead-lettering. Wrap each action and drop malformed ones.
- **Processing rules `_exec_run_agent`** sets `agent_running` via raw UPDATE (bypassing `transition_card_status`) *before* the session exists; a spawn failure strands the card until restart (`processing_rules.py:445-489`).
- **Executor**: an unexpected exception after transitioning to `executing` escapes with the card stranded until the startup sweep (`executor.py:87-149`). Wrap and transition to `failed`.
- **Omni `_append_to_recent` mutates the shared cached snapshot before commit** (`omni.py:382-526`): on a failed commit the cache serves phantom state and re-processing double-appends. Deep-copy on cache hit; install into cache only after commit.
- **Config**: `load_settings` leaks mutable references to `DEFAULT_SETTINGS` (in-place mutation corrupts process-wide defaults) and `save_settings(load_settings())` freezes all defaults into the user file so future default changes never reach existing installs (`config.py:218-247`). Deep-copy + persist only the delta.

### API / DB

- **`/cards/grouped` is unbounded**: no LIMIT, ships `intelligence`/`staged_output`/`suggested_actions` JSON for every matching card, and the UI drops the `date` filter entirely in bookmarked/related/all-days modes — a multi-MB response and multi-second stall that blocks the shared DB connection. Cap distinct groups server-side.
- **Feed search bypasses FTS5**: each term expands to 16 `LIKE` predicates over large JSON columns plus a *correlated tag subquery per row per term* (`cards_api.py:271-296`) while `cards_fts` sits unused by the feed. Route feed search through the existing `build_fts_match`.
- **Dashboard queries statuses migration 022 deleted**: `status = 'approved'` etc. (`dashboard_api.py:86-89,190-198`) — approval metrics are permanently zero; the dashboard silently lies.
- **WS broadcast prunes the wrong connections**: it zips the *current* mutated list against results from a pre-await snapshot (`websocket.py:36-44`) — a client that connects during a broadcast (chat streams broadcast per chunk) gets silently dropped from live updates. Zip the snapshot; make `disconnect` tolerant.
- **Status-transition races**: `transition_card_status` is validate-then-write with no `AND status = ?` guard, and the agent streaming paths write statuses with raw UPDATEs bypassing validation — a dismissed card can be resurrected when its agent finishes.
- **The single shared aiosqlite connection gives zero transaction isolation**: any task's `commit()` commits *everyone's* pending statements; there is no `rollback()` anywhere in the engine, and the "ONE transaction" comment in `_persist_card` is false. Guard the few multi-statement invariants (card cascade, space delete, merge) with a lock + explicit BEGIN/COMMIT/ROLLBACK.
- **Migrations are non-atomic** (`migrate.py:52-59`): a mid-file failure commits half the DDL without recording `schema_version` — next startup re-runs the file and dies on the first non-idempotent statement, a boot loop needing manual surgery.
- **`_polishing` flag persisted with no startup sweep** (`cards_api.py:1359`): restart mid-polish permanently 409s that action and seeds an eternal spinner.
- **`dismiss_group` doesn't handle `ctx_` ids** (asymmetric with `mark_group_read`) — latent trap for the next UI wire-up.
- **Prev/next date navigation** applies *today's* UTC offset to historical dates (DST bug) and runs two full-table `DATE()` scans per feed load (`cards_api.py:574-595`).
- **Card hard-delete orphans `tag_assignments` and `group_summaries.card_ids`** (`cards_api.py:985-1003`).

### Egress

- **SMTP is dead end-to-end**: `_load_credentials` reads keychain key `smtp:default` but creds are stored under `smtp:{connection_id}` (`connections.py:502`) — connect validates fine, every send fails. Also hardcodes STARTTLS, breaking port-465 providers that pass validation.
- **`validate_payload` is never called by any execution path** — all 10 adapters' validation (~250 lines) is dead; invalid payloads travel to n8n and fail generically. One insertion point in `enrich_payload_from_event` covers card, composer, and chat paths.
- **Timeouts double-send**: n8n executor workflows run to completion after the engine's 30s POST timeout, but the result is marked `retryable=True` and retry deletes the action log and re-executes — second email sent. Treat timeout as outcome-unknown; the stable `egr_{card_id}` action_id could dedupe in the workflows.
- **The gmail→`google_calendar` remap 404s on every cloned-workflow install** (`executor.py:96-100`, `router.py:99-101`): it resolves to a hyphenated default path that doesn't exist; remap to platform `calendar` which resolves via sources.
- **Chat-driven egress never sets `connection_id`** — with two accounts it silently uses the oldest (`executor_rows[0]`), the exact wrong-account class fixed in the composer; and chat-path Jira actions never get `jira_base_url`, so the executor falls back to the literal `your-domain.atlassian.net`. The composer fix itself has a residual hole: the named-but-unmatched raise only triggers when `executor_rows` is non-empty.
- **OAuth health never flags dead connections**: refresh failure keeps `status="connected"` and even bumps `last_validated_at` (`health.py:95-97`); and `_update_n8n_oauth_token` no-ops against an n8n API endpoint that doesn't exist — engine and n8n independently refresh the same grant (rotation drift risk on Microsoft).
- **Gmail header injection**: `build_api_payload` interpolates subject/recipients into raw RFC 2822 with no CRLF stripping (`gmail.py:264-285`) — inbound-controlled subjects can inject headers into mail sent from the user's account. One-line sanitize.
- **Execution idempotency rests on a non-atomic check-then-act**; `/egress/execute` (composer) has no dedup at all. Make the `executing` transition atomic (`UPDATE … WHERE status IN (...)`, check rowcount).
- **`handle_callback` doesn't wrap workflow cloning in try/except** (`oauth.py:339-345`): a mid-clone exception leaves keychain tokens + active cloned ingestion workflows with no connection row — invisible in Settings, double-ingesting after reconnect.
- **`load_team()` NameError swallowed** by bare except in `egress_api.py:460` — team members never appear in field suggestions.

### Agents / MCP / Tauri shell

- **WS agent input is a silent no-op**: stdin is spawned as `DEVNULL`, so `send_input` drops text while the UI records the input as delivered; the real path is `resume_with_answer`. Delete or redirect the dead handlers (`ws_router.py:64,81,99`).
- **Resume paths have no running-state guard and drop reverse mappings** (`session_manager.py:468`, `workspace_api.py:186,360`): resuming a live session spawns a second subprocess against the same session id; resumed sessions escape `cancel_sessions_for_card`, so agents keep running after their card is archived.
- **No Host validation on the engine**: DNS rebinding defeats CORS and exposes the unauthenticated REST API including `GET /mcp/token/reveal`; body-less `POST /mcp/token/refresh` is CSRF-able. One `TrustedHostMiddleware` line closes both.
- **MCP bearer token and full prompt passed in argv** (`claude_code.py:96-97,170-171`) — visible in `ps`. Use a 0600 temp file for `--mcp-config`.
- **n8n has no parent watchdog**: app crash orphans it (webhooks firing at a dead engine); a non-n8n process on 45678 makes startup fail invisibly (`n8n.rs:646-700`).
- **Port reclaim kills whatever holds 8420/45678 without identity check** (`main.py:137`, `lib.rs:494`). Verify the victim is a Laya process first.
- **MCP Streamable sessions leak** a task+transport per abandoned `initialize` until restart; `_stderr_lines` grows unbounded (use a deque).

### UI

- **Every search keystroke triggers a full backend refetch**: `loadGroups` reads `searchQuery` before its first await (`feed/+page.svelte:487`), silently making it a tracked dependency of the reload effect at :591 — typing "hello world" fires ~11 full `GET /cards/grouped` reloads and defeats the intentional 300ms debounce. Wrap the parse in `untrack()`.
- **No resync after WebSocket reconnect**: the store wipes its buffer on disconnect and nothing watches the `disconnected→connected` transition — after an engine restart the feed is silently stale until the user changes filters. Watch `wsStatus` and `scheduleReload()` on reconnect.
- **Card mutations fail completely silently**: markDone/dismiss/archive/reopen/bookmark use try/finally with no catch across ActionCard/CardDetail/ListRow — the button just "doesn't work" on error. Also four components hardcode optimistic reopen statuses (`'pending'`/`'ready'`) that contradict the backend's dynamic restore logic.
- **Omni page**: errors after first load can never render (gated `{#if error && !snapshot}`), the `omni_updated` handler ignores `space_id` and yanks users off historical snapshots, and space-switching has no stale-response guard.
- **WS receive loop blocks for the whole streamed chat** (`main.py:438-441`): approve/deny/cancel messages queue unprocessed for the duration — the user cannot cancel a slow local-model chat. Dispatch `handle_ws_message` as a task.
- **Day Summary modal fires 2-3 duplicate fetches per open** (three overlapping triggers); **CardDetail refetches related/egress context on every WS status tick** of the selected card (prop identity change, not card change).
- **Coherence page**: rerun lacks the concurrent-trace guard, a WS latch gap spuriously shows the "New events detected" rerun banner (inviting a needless LLM-cost rerun), and generation errors only `console.error`.
- **Single-key shortcuts ignore open modals**; `closeWebSocket` re-arms a zombie reconnect; `sub_groups` WS-patching is dead code (backend never populates it); `WsMessage` union in types.ts omits five handled message types.

---

## 3. LLM-request & token efficiency

**Measured per-event cost (main pipeline):** 2 blocking calls (router + stager) for the common case; +1–2 persona worker calls when `requires_research`; async add-ons of 0–1 context-link confirm, 0–1 group-summary roll (plus a possible cascaded context-group regen), amortized ~0.5 daily-summary fold, ~1/50 omni. Typical total ≈ 2–4; worst ≈ 7, with ×3 tenacity and ×3 queue-retry multipliers on failure paths. Fixed prompt overhead is ~5.5–6K tokens/event (router ~1.6K + stager ~3.3K systems) before any event content.

**Approximate prompt sizes (chars/4):**

| Prompt (system) | ~tokens | Notes |
|---|---|---|
| chat tool definitions | **8,400** | 40 tools, resent every turn + every tool-loop iteration (cap 20) |
| stager | **3,320** (+470 schema) | every event |
| omni resynthesis | 2,155 | + snapshot JSON + up to 150 card lines |
| router | 1,590 | every event (also batch) |
| chat system | 1,500 | + card_context/identity appended |
| summarizer (daily) | 970 | + full current-summary JSON per batch |
| group_summary initial/rolling/context | 455 / 430 / 415 | |
| personas | 175–485 | only run when `requires_research` — good gating |

### Calls that can be removed outright (zero prompt growth)

1. Wire `context_match` through (§1.1) — kills the redundant confirm call per grouped card.
2. Reuse persisted `router_output` on queue retries.
3. Move the briefing duplicate-day check before generation.
4. Fix the debounce-cancel bugs — currently wasted generations *and* lost data.
5. Skip the context-summary cascade when the entity summary's headline/status came back unchanged (string compare) — currently every grouped-entity update pays double.
6. Filter tenacity retries to genuinely-retryable errors (`client.py:844-857`) — today deterministic 400s can burn 9–18 doomed HTTP calls per event across nested retry layers.
7. Share one ChromaDB search per event: router, stager, and each worker independently embed-and-search the same `title + body[:300]` (2–4 identical searches/event). Embedding cost, not LLM, but it's on the hot path.

### Token reductions (local-model safe — all shrink or hold prompt size)

- **Chat tool definitions are the single largest cost: ~8.4K tokens, resent on every turn and every tool-loop iteration.** On an 8K local context, tools+system exceed the window before the user's question. Split the toolset: always send the ~16 read/card-write tools (~4K); gate rules/settings/egress groups behind a cheap intent check. Trim descriptions that restate parameter docs; move operator/field enums into `get_rule_options` output (the prompt already mandates calling it first).
- **Stager system prompt (~3.3K tok/event) is ~45% conditionally irrelevant**: email-phishing guidance ships on every Jira event, Jira/PR-lifecycle guidance on every email, and ~900 tokens of role-context *explanation* duplicate the operative per-event directive. Build 3–4 per-platform-family variants — each stays a stable cache prefix; non-email events save ~1.2–1.5K tokens. Same pattern for the router (~600 tok of Jira/Bitbucket guidance on Gmail/Slack events).
- **Router output**: require an empty `research_plan` when `requires_research=false` — on small models the 3–5-step plan is the dominant output cost for bot-noise events.
- **Prompt caching is half-wired**: only tools+system get an Anthropic cache breakpoint, and `_inject_current_datetime` stamps a seconds-granular timestamp per loop iteration, churning the prefix. Freeze the timestamp per request and add a second breakpoint on the final message — a 20-iteration tool loop converts ~90% of input to 0.1× cache reads.
- **Cap tool results before appending to chat messages**: tools allow `limit` up to 200 with full `intelligence`/`content_body` text; a single call can blow a local context (only the log preview is truncated today). Cap serialized results at ~8–16K chars with a "truncated, use offset" suffix.
- **Omni resynthesis is the most truncation-prone call**: full snapshot + up to 150 cards ≈ 8–15K tokens in one prompt; on failure the backlog grows and the next attempt is *bigger*. Fold in chunks of ~30–50 across sequential smaller calls — more calls, each strictly smaller; the right trade for local models.
- **Ambient chat retrieval overlaps the tool loop**: every turn (including "thanks") pays embedding + ChromaDB + 3 SQL searches and injects up to 3K tokens the model then often re-fetches via tools. Shrink the ambient budget; skip it when the previous assistant turn used tools.

### Accounting bugs that hide the waste

The truncation-doubling retry overwrites instead of adding output tokens (`client.py:926`); `get_current_month_cost` filters `success=1`, making JSON-failure loops (the documented Gemma spiral) invisible to the budget cap; streaming logs chars/4 estimates that exclude the entire tool block (use `stream_options.include_usage`).

---

## 4. Performance (CPU / memory / event loop)

- **`embed_document` blocks the event loop on every emit** (`chromadb_store.py:250-266`): sync SentenceTransformer encode (50–500ms CPU, ~2s first-load) + Chroma upsert run inline, while `memory_search` in the same file correctly uses an executor. Every card emit freezes all API/WS traffic; bursts serialize N stalls. Same for `delete_document`. Independently flagged by three reviewers — the top performance fix.
- **Sync keychain reads on the loop, uncached**: every Jira execution, n8n API call, webhook-cache refresh, and health sweep does a blocking ~10–100ms `keyring` call (`connections.py`, `backends/n8n.py:269`, `oauth.py:132`). A tiny TTL cache invalidated on store/delete removes the only blocking IO in the egress hot path.
- **`load_settings()` re-reads and re-merges settings.json ~10× per event** plus per scheduler tick, per budget check, per rule firing. Fix with an mtime-keyed cache (also fixes the mutable-defaults leak, §2 Config).
- **FTS churn is O(N²) per thread**: the carry-forward `UPDATE … WHERE entity_id = ?` rewrites every sibling row on each emit, and `cards_fts_au` is an unconditional AFTER UPDATE trigger — a 50-card thread pays 50 FTS delete+reinserts per new message, and again per status change. Add `AND group_active_at < ?` to the update and scope the trigger to the indexed columns (`emit.py:265-272`, `db/fts.py:106-111`).
- **Feed broadcast waits behind an inline LLM call**: the new-card WS message is sent after context grouping (LLM confirm) and entity resolution — the user sees new cards seconds-to-tens-of-seconds late while a pipeline semaphore slot is held. Broadcast right after embed; run grouping post-broadcast with a `card_updated` follow-up (`emit.py:706-748`).
- **N+1 / unbounded queries**: `_find_linked_entities` does nested per-ref `LIKE '%…%'` full scans (`trace.py:951-993`); fuzzy `find_contact` fetches every matching *event* row before deduping to 20 (`contact_tools.py:101-116`); prev/next date navigation runs two full-table `DATE()` scans per feed load.
- **Unbounded memory**: `_stderr_lines` grows for the process lifetime; MCP Streamable sessions leak until restart; omni's `_latest_cache` mutation-before-commit can serve phantom state.
- **Chat history ordering has 1-second resolution and no tiebreaker** (`chat.py:294-303`): user+assistant pairs share a timestamp; add `rowid DESC` tiebreaker. The 10-message window is count-based, not token-based — one 65K assistant answer re-enters the prompt verbatim for 10 turns.

---

## 5. Design & code-redundancy map

Ranked by leverage; each has already produced or is about to produce drift bugs:

1. **Five persona workers are near-verbatim copies** (~250 lines): comms/sales/hr differ only in prompt builder, schema, and fallback dict; drift already visible (engineer takes `space_id`, others don't; ops/finance silently drop `participant_roles`). Replace with one `run_persona_worker(spec)` driven by a per-persona table; the identity-block builder and `_summarize_findings` are also triplicated across their prompt files.
2. **Four CLI agent adapters duplicate ~380 lines** (exit-code epilogue ×4, `_make_event`, control methods, approval patterns) and have drifted (only Claude handles `rate_limit_event`, only Claude maps truncation). Move the lifecycle into `base.py`; adapters keep spawn-args and line parsing.
3. **Three parallel retrieval stacks** (chat.py / trace.py / tools): stopwords copy-pasted 4×, RRF duplicated, the "FTS-else-LIKE" pattern re-implemented 5× with divergent semantics — `search_cards` is OR on the FTS path but AND on the LIKE path while the tool description promises AND. A single `retrieval.py` (~400 lines removed) makes the divergence impossible.
4. **`cards_api.py` (2,800 lines)** splits along six seams: feed/grouping, lifecycle, action-payload/polish, agent-run (two near-identical 120-line streaming functions), context groups, group summaries. The 30-column card SELECT is duplicated 4× and has already drifted (trace_api omits `read_at`/`context_id`, silently masked by `_row_to_card`'s guards). `reopen_card` bypasses the lifecycle SSOT with four duplicated update/broadcast blocks.
5. **`feed/+page.svelte` (2,617 lines)**: extract the WS reducer (lines 642-873 — most bug-dense, becomes unit-testable as a pure function), the FLIP/layout engine (a 350ms "predict→transition→correct" block copy-pasted ×3), the filter popover, summary modal, and drawer. Badge/status/priority maps are copy-pasted ~9× across components *with divergent values*; `timeAgo` variants exist in ~15 files.
6. **`llm_call_streaming` is a drifted ~200-line copy of `llm_call`** — already missing the max_tokens clamp (streaming chat sends 65,536 to strict vLLM servers, the exact 400 the clamp was built for), real usage accounting, and the agent-model guard. Extract a shared `_prepare_call_kwargs()`.
7. **Dead code to delete**: `confirm_entity_link` (Layer 3), `oauth._setup_n8n_workflows` (157 lines, contains the third copy of the credential-injection matcher whose other two copies say "must be kept in sync"), the `pipeline.llm_retries` setting (silently ignored end-to-end — `_get_pipeline_settings` never includes the key), UI `sub_groups` patching (backend never populates it), `selectingTrace`. `_generate_password` should be *used*, not deleted (§1.5).
8. **Rust shell**: ~250 lines of process/path plumbing duplicated across `lib/sidecar/n8n/runtime.rs` (four SIGTERM→poll→SIGKILL loops, ×3 `home_dir`, ×2 env sanitizers) — one `process_util.rs` collapses it.
9. **`learn.py` / `context_learn.py`** are structurally near-identical and would share a driver once consolidation is ported to the classification side (§2). Tuning defaults are duplicated between `DEFAULT_SETTINGS["tuning"]` and inline `get_tuning(key, default)` literals at every call site.
10. **Agent-tier rough edges** (`agent_backend.py`): schema-retry feedback references "your previous response" but every attempt is a fresh process (blind re-roll — include the invalid output snippet); `max_tokens`/`temperature` silently ignored; truncation invisible for non-Claude tiers; `_strip_think_blocks`/`_extract_json` duplicated (different implementations) between client.py and agent_backend.py.

---

## 6. Security notes (localhost-calibrated)

Beyond the n8n password (§1.5): no `TrustedHostMiddleware` on the engine, so DNS rebinding defeats CORS and exposes the unauthenticated REST API including `GET /mcp/token/reveal`, and the body-less `POST /mcp/token/refresh` is CSRF-able from any website (one middleware line closes both). The MCP bearer token and full card content are passed in `claude` argv (visible in `ps` — use a 0600 temp file for `--mcp-config`). Port reclaim SIGTERMs whatever holds 8420/45678 without verifying it's Laya's process. n8n has no parent watchdog, so an app crash orphans it. And the Gmail CRLF injection from §2.

Verified clean: engine binds 127.0.0.1 with a restrictive CORS allowlist, MCP defaults to bearer auth with per-call scope enforcement and no bypass found, keychain wrappers degrade gracefully when locked, runtime downloads are SHA-256-verified, and no SQL injection exists anywhere (all f-string queries interpolate only placeholders/typed values).

---

## 7. Suggested priority order

1. **Quick wins, big payoff (each < a day):** `context_match` threading (§1.1) · ChromaDB delete doc-id + housekeeping (§1.2) · reorder route order (§1.4) · n8n random password (§1.5) · $0 pricing for local/agent models (§1.6) · briefing check-before-generate · `>=` scheduler triggers · `run_in_executor` for `embed_document`.
2. **Correctness under load:** queue reaper/wedge fix (§1.3) · debounce-cancel and summarize race fixes · batch-router feedback + index matching (§1.7) · atomic status transitions · subprocess line limit (§1.10) · manual workspace resume (§1.9).
3. **Token program:** chat toolset split → stager per-platform variants → router research_plan skip → cache-breakpoint/timestamp fix → tool-result caps → chunked omni resynthesis.
4. **Refactor program (in this order):** retrieval.py consolidation → persona worker spec table → agent adapter base → llm_call/streaming merge → cards_api split → feed page extraction.

---

## 8. What's already good (keep as-is)

- Durable event queue: `INSERT OR IGNORE` idempotency on n8n re-delivery, atomic claims, exponential backoff, dead-lettering, startup recovery for events *and* provisional cards.
- Omni's crash-safe durable queue (enqueued in the card-insert transaction, zero inline LLM cost, amortized resynthesis) — a genuinely good design.
- Daily-summary batching (cap 10/call) and thread-context reuse of group summaries (zero-LLM contextual embeddings).
- Persona workers correctly gated on `requires_research`; datetime injected into the user message (not system) is deliberately cache-safe; omni input bounded by schema-enforced `maxItems`, not just prose.
- FTS5 query building correctly escaped (no MATCH-syntax crash vector) with a working LIKE fallback; FTS trigger coverage is complete (no `INSERT OR REPLACE` anywhere, so the classic REPLACE-skips-trigger drift can't occur).
- The padding/truncation salvage ladder (`_extract_json`/`_complete_json`, stop sequences, windowed repeat_penalty) is opt-in and correctly scoped to custom providers.
- Batch-routing circuit breaker + local-provider skip; processing-rules defensive layers (rate limits, per-card firing cap, auto-disable, condition-depth cap); housekeeping covers every growth-prone SQLite table (ChromaDB excepted — §1.2).
- The egress `Platform` adapter layering with shared enrichment between preview and execute (the two can't drift); version-gated n8n workflow sync (no redundant HTTP churn); the wrong-account fix in `_resolve_webhook_url` is sound for the path it covers.
- `engine.ts` single-`request()` API client; feed's hybrid incremental-patch/full-reload WS strategy with fetch-id guards and FLIP smoothing; all feed-page listeners/observers/timers cleaned up on destroy.
