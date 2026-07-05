# Laya — Code Review (July 2026) Remediation Plan

**Source:** [`code-review-2026-07.md`](./code-review-2026-07.md) (review dated 2026-07-02)
**Plan owner:** engineering
**Status:** in progress — see Implementation Status below

---

## 0. Implementation status (updated 2026-07-03)

Work landed on branch **`code-review-2026-07-remediation`** (local commits, not pushed), each phase a separate commit, full engine test suite green (856 passing) after every batch.

**✅ Complete & tested:**
- **Phase 1** — all 12 quick wins (P1-1…P1-12). Note: P1-12 `llm_retries` was *wired* (not deleted) since it had heavy test coupling and the real defect was that it was ignored.
- **Phase 2** — all security items (P2-1 TrustedHost+CSRF, P2-2 MCP token temp file, P2-3 port-reclaim identity check, P2-4 n8n port-conflict visibility). Rust `cargo check` green.
- **Phase 3** — all 9 correctness-under-load items (P3-1 queue wedge/reaper … P3-9 executor guard), plus new batch-router alignment tests.
- **Phase 4 (mostly done)** — P4-1, P4-2, P4-4, P4-5, P4-6, P4-7, P4-8, P4-11, P4-13, P4-14, P4-15, P4-16, P4-17, P4-18, P4-19, P4-20, P4-21, P4-22, P4-23, P4-25, P4-27, P4-28. **P4-12** (multi-statement invariant guard: new `db/sqlite.transaction()` — a module `asyncio.Lock` serializing guarded invariants against each other + commit-on-success/rollback-on-failure so a mid-sequence failure undoes its own uncommitted writes instead of leaking them into the next `commit()`; applied to the 5 low-frequency, off-hot-path invariants: space delete, card cascade delete (vector cleanup moved outside the lock; housekeeping now commits per-card), context-group merge + both unlinks. Deliberately NOT applied to the hot `_persist_card` emit path — serializing it needs load testing — and NOT full per-request isolation, which would need a connection pool. Covered by new `test_db_transaction.py` (commit/rollback/shared-conn + cascade completeness)). **P4-10** (feed-search perf, *perf-only per user decision — matching semantics unchanged*: de-correlated the per-row-per-term tag `GROUP_CONCAT` subquery into one hoisted `LEFT JOIN … GROUP BY` (`ctag.tag_names`), and dropped the giant `staged_output`/`suggested_actions` JSON blobs from the scanned fields. Substring `LIKE` kept — "pay" still matches "payment" — so no user-visible search change beyond the two dropped action-JSON fields. Covered by new `TestFeedSearch` (substring preserved / tag match / dropped-field)). **P4-9 *part a*** (payload slim: `/cards/grouped` now ships a slim list card — `_row_to_card(slim=True)` drops the two large JSON blobs `staged_output`/`suggested_actions`; the detail panel already re-fetches the full card via `GET /cards/{id}` (`feed selectCard`), so the detail view is unaffected. `intelligence` deliberately **kept** — it's small and keeps client-side search + `GroupSummaryDetail` working without a refetch, matching P4-10's server-side scan. Coordinated UI fixes for the 3 direct grouped-card consumers: session-restore now hydrates via `getCard`, the `action_payload_updated` WS handler patches `selectedCard` directly, and `cardMatchesSearch` drops the two slimmed fields. Verified offline: new `TestGroupedPayloadSlim` (list slim / detail full) + `svelte-check` 297 files 0 errors. Part b — the group cap + load-more pagination — is the remaining half). Plus the **UI batch verified against the running app**: P4-29 (search-debounce untrack), P4-30 (WS reconnect resync), P4-31 (reopen status + mutation catches), P4-32 (omni error/space/version/stale), P4-33 (WS receive loop → tasks; verified live), P4-34 (Day Summary dedupe + CardDetail refetch guard), P4-35 (coherence guards), P4-36 (shortcuts/ws-close/WsMessage). P4-24's core (atomic `executing`) landed via P3-8.
- **Phase 5 (complete)** — P5-1 (=P1-8), P5-2 keychain TTL cache, P5-3 settings mtime cache, P5-4 FTS O(N²) churn, P5-5 feed broadcast before grouping, P5-6 N+1 (contact + trace), P5-8 chat ordering tiebreaker. P5-7 subsumed by P4-6/P4-28.
- **Phase 6 (accounting/retry done)** — P6-1/2/3/4 (subsumed by P1-1/P4-1/P1-6/P3-6), P6-6 tenacity retry filter, P6-15 token-accounting add, P6-16 failed-call cost counted.
- **Phase 7 (started)** — P7-1 *part 1* (shared retrieval primitives: `laya/retrieval.py` with STOPWORDS + extract_keywords + reciprocal_rank_fusion; fixed the RRF doc-id drift and the search_cards FTS-OR/LIKE-AND divergence via `build_fts_match(match_all=...)`). P7-1 *part 2* (folded the try-FTS-then-fall-back-to-LIKE dispatch — copy-pasted verbatim across chat's card+event search, trace's event search, and the card-search tool, where the warn-labels/guards had begun to drift — into `retrieval.fts_or_like(query, *, min_len, max_terms, match_all, fts, like, warn_event)`. Behavior-preserving: each stack keeps its own tuned min_len/match_all and its own divergent FTS/LIKE query bodies + result shapes; only the fallback control-flow (incl. the exception→LIKE safety net) is shared. Covered by `test_fts.py` end-to-end across all 3 stacks + 5 new `fts_or_like` unit tests). P7-2 (spec-driven persona worker: 5 near-verbatim drafting workers → `workers/persona.py` run_persona_worker + PERSONA_SPECS; hoisted the triplicated `_summarize_findings`; ops/finance drift now explicit config). P7-3 (concrete `BaseCodingAgent` in agents/base.py holds the shared lifecycle — `_make_event`, control methods, exit-code epilogue — for the 4 CLI adapters; Claude's MCP cleanup is now an `_on_process_exit` hook). P7-9 (removed the dead 157-line `oauth._setup_n8n_workflows` + its third copy of the credential-injection matcher; shared `_PLATFORM_HTTP_CRED_TYPES` dict kept). P7-10 (Rust `process_util.rs`: `home_dir`/`laya_home` ×3 and the LD_LIBRARY_PATH scrub ×2 collapsed to one copy each; the shutdown SIGTERM loops left for a live-verified pass). P7-4 (route status writes through `transition_card_status`: `allow_restore` flag; reopen_card's 4 blocks → 1 SSOT call; card_tools + pipeline-fail routed; agent-streaming writes left for P7-6). P7-5 (merged `llm_call`/`llm_call_streaming` prep into `_prepare_call_kwargs` — streaming now gets the max_tokens clamp it was missing). P7-8 *safe parts* (centralized tuning defaults — dropped the 10 inline `get_tuning(key, literal)` defaults so `DEFAULT_SETTINGS["tuning"]` is the single source, adding the previously-missing `classification_rules_max_injection` key; extracted the identical `get_spaces_with_unprocessed` bodies into `pipeline/learn_common.query_spaces_with_unprocessed(table, threshold, log_event)`. The full extraction-loop driver merge + consolidation-port left deferred — see below).

**⏳ Remaining (not yet done):**
- **Phase 4** — P4-9 *part b* (the `/cards/grouped` group **cap + pagination / load-more** — backend `limit`/`offset` + `has_more` and the feed load-more UI; part a, the payload slim, is done — see below), P4-24 composer `/egress/execute` dedup (core covered by P3-8); P4-26 (WS `send_input` no-op — the workspace agent-answer flow, wants agent-driven verification).
- **Phase 6** — the token-*reduction*/prompt-engineering items P6-5, P6-7, P6-8, P6-9, P6-10, P6-11, P6-12, P6-13, P6-14, P6-17 (need prompt-audit + golden-output harness per working principle #4).
- **Phase 7** — P7-6 (split the 2,800-line `cards_api.py` + merge the two agent-streaming functions), P7-7 (extract the 2,617-line `feed/+page.svelte`), P7-8 *remaining* (the full extraction-loop driver merge over `run_learn_extraction`/`run_context_learn_extraction` — a multi-callback abstraction over columns/prompts/schemas/insert-tables for only 2 callers in a delicate LLM area — plus porting context-rules' LLM consolidation pass to the classification side, which is the deferred P4-8 half).

Remaining: (a) P4-9 *part b* (`/cards/grouped` group cap + load-more pagination — part a payload-slim done; product call made: "cap + load more"; needs the feed load-more UI + browser verification), (b) the big structural splits (P7-6/7), (c) the deferred P7-8 driver/consolidation half and the P6 prompt program.

---

This plan turns every comment in the review into a concrete, sequenced, independently-shippable task. It deduplicates findings that the review lists in more than one section (e.g. `embed_document` blocking, `TrustedHostMiddleware`, MCP-token-in-argv) into a single task with cross-references. A [coverage matrix](#12-coverage-matrix) at the end proves every review subsection maps to at least one task.

---

## 1. Working principles (apply to every task)

These encode how we already work on Laya and should govern each fix:

1. **One scoped change per PR/turn.** Do not bundle unrelated fixes. Each task below is sized to ship alone.
2. **Reproduce-first, verify-after.** Before writing a fix, confirm the bug still reproduces on `main` (the review is accurate but code moves). After the fix, drive the real flow (not just a unit test) to observe the corrected behavior.
3. **Forward-fix, no backfill.** For data that ages out (summaries, caches, ghost embeddings past retention), a forward fix + roll-over is enough; do not write migrations/sanitizers to scrub already-polluted historical rows unless the row is load-bearing forever (e.g. the `entities` unique-constraint dedupe).
4. **Investigation-first for refactors and prompt cuts.** The token program (§8) and refactor program (§9) require a data-first pass — capture current sizes/behavior, add a regression harness, *then* change. Audit each LLM prompt before proposing cuts.
5. **Hydrate from DB, not the LLM.** Where a field can be resolved deterministically from SQLite, prefer that over threading it through prompts.
6. **Comment the "why."** Every defensive fix / workaround gets an inline comment explaining what breaks without it.
7. **Convention reminders:** next SQLite migration number is **072** (001–071 exist); bump `meta.laya_version` on any edited `n8n/workflows/*.json`; async everywhere in the engine; Svelte 5 runes only in the UI.

---

## 2. Phase overview

| Phase | Theme | Task count | Gating |
|---|---|---|---|
| **P1** | Quick wins (one-line / small, high payoff) | 12 | none — start immediately |
| **P2** | Security hardening (cheap, urgent) | 6 | none |
| **P3** | Correctness under load (queue / status / races) | 9 | some depend on P7 SSOT work; noted per task |
| **P4** | Functional bug sweep (pipeline, API/DB, egress, agents, UI) | 38 | independent; group by domain |
| **P5** | Performance & event-loop | 8 | P5-1 (embed executor) also in P1 |
| **P6** | Token & LLM-cost program | 13 | investigation-first |
| **P7** | Refactor & de-duplication program | 10 | strict internal order |

Recommended calendar order: **P1 → P2 → P3 → (P4 ∥ P5) → P6 → P7**. P4 and P5 can run in parallel across contributors. P7 refactors should land *before or alongside* the token program where they share files (retrieval, llm_call), to avoid fixing then deleting the same code.

Effort key: **S** ≤ half day · **M** ~1–3 days · **L** multi-day / touches many files.

---

## 3. Phase 1 — Quick wins

The review's §7.1 "quick wins, big payoff (each < a day)" list, plus adjacent one-liners.

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P1-1** | Stager `context_match` discarded → wasted confirm-link LLM call per grouped card (§1.1, §3.1) | `models/*` `ActionCardData`, `pipeline/stager.py` `_parse_stager_response`, `pipeline/emit.py:426-467` | Add `context_match: dict \| None` to `ActionCardData`; populate in parse; consume at `emit.py:426` so the `hard_gate` path (432-467) executes and the fallback ChromaDB+`confirm_context_link` call is skipped. Add a test asserting no confirm call when `context_match` present. | S |
| **P1-2** | Deleted cards' ChromaDB docs never removed (doc-id double-prefix) + no ChromaDB housekeeping (§1.2, §4) | `api/cards_api.py:1035`, `pipeline/emit.py:386,624`, `pipeline/scheduler.py:34-62` | Fix delete id: docs are indexed under `card_<hex>`, so delete `card_id` (already prefixed), not `f"card_{card_id}"`. Add a ChromaDB retention sweep to housekeeping mirroring SQLite retention. | S |
| **P1-3** | `PUT /processing-rules/reorder` unreachable — matched by `{rule_id}` route (§1.4) | `api/processing_rules_api.py:367,458` | Move the static `reorder` route declaration **above** `PUT /{rule_id}`. Add a test hitting `/reorder`. | S |
| **P1-4** | n8n owner uses hardcoded repo-public password (§1.5, §5.7, §6) | `n8n_bootstrap.py:25,829` | `stored_password or _generate_password()` — the existing keychain-persist path already saves whatever password is used. Removes dead `_generate_password`. Existing installs: on next boot they keep the stored password; only fresh installs get a random one (acceptable — document it). | S |
| **P1-5** | Phantom budget $ from local/agent models can pause all ingestion (§1.6) | budget pricing (`pipeline/budget.py` / pricing table), `get_current_month_cost` | Price unrecognized local/custom models at `$0`; exclude `agent/%` model rows from the dollar aggregate (they're governed by `agent_budget.py`). | S |
| **P1-6** | Briefing duplicate-day check runs *after* the LLM call (§2 pipeline, §3.3) | `pipeline/briefing.py:128-165` | Reorder the already-generated-today guard **before** generation. Free LLM savings. | S |
| **P1-7** | Scheduler daily jobs skip when tick misses exact minute; month-rollover is dead code (§1.8) | `pipeline/scheduler.py:281,299-303`, `main.py:265-289` | Change `hour == t and minute == t` to `>= target` guarded by the existing per-day date guard; spawn briefing/omni/learn as tasks (don't await inline). Separately: implement the month-rollover snapshot (both branches currently `pass`) so monthly budget history survives a midnight-of-the-1st gap. | S–M |
| **P1-8** | `embed_document` blocks the event loop on every emit (§1.2-adjacent, §4, §5.1-perf) | `chromadb_store.py:250-266` | Wrap the sync SentenceTransformer encode + Chroma upsert in `run_in_executor` (mirror the existing `memory_search` pattern in the same file). Same for `delete_document`. **Top single performance fix.** | S |
| **P1-9** | Gmail header injection via un-stripped CRLF in RFC 2822 build (§2 egress, §6) | `egress/platforms/gmail.py:264-285` | Strip `\r`/`\n` from subject + recipient fields before interpolation. One-line sanitize + comment. | S |
| **P1-10** | Dashboard queries statuses that migration 022 deleted → approval metrics always 0 (§2 API) | `api/dashboard_api.py:86-89,190-198` | Replace `status='approved'` etc. with the current v2 status vocabulary (`done`/`dismissed`/…). Verify metrics render non-zero. | S |
| **P1-11** | `load_team()` NameError swallowed by bare except → team never in field suggestions (§2 egress) | `api/egress_api.py:460` | Narrow the except, fix the underlying NameError, log on failure. | S |
| **P1-12** | Dead-code deletions safe to do now (§5.7) | various | Delete: `confirm_entity_link` (Layer 3) + its prompt, UI `sub_groups` WS-patching, `selectingTrace`, the ignored `pipeline.llm_retries` setting path. **Keep** `_generate_password` (now used by P1-4). `oauth._setup_n8n_workflows` (157 lines) deferred to P7-9 because it holds a credential-injection matcher copy. | S |

---

## 4. Phase 2 — Security hardening

Cheap, mostly one-liners, high value; do right after P1. (n8n password is P1-4.)

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P2-1** | No `TrustedHostMiddleware` → DNS rebinding defeats CORS, exposes REST incl. `GET /mcp/token/reveal`; body-less `POST /mcp/token/refresh` CSRF-able (§2 agents, §6) | engine app setup (`main.py`) | Add `TrustedHostMiddleware` allowlisting `127.0.0.1`/`localhost`. Require a body/token on `/mcp/token/refresh`. One middleware line closes both. | S |
| **P2-2** | MCP bearer token + full prompt passed in `claude` argv (visible in `ps`) (§2 agents, §6) | `agents/claude_code.py:96-97,170-171` | Write `--mcp-config` to a `0600` temp file instead of argv; pass prompt via stdin/file. | S–M |
| **P2-3** | Port reclaim SIGTERMs whatever holds 8420/45678 without identity check (§2 agents, §6) | `main.py:137`, `src-tauri/.../lib.rs:494` | Verify the victim process is Laya's engine/n8n (cmdline/exe check) before killing. | M |
| **P2-4** | n8n has no parent watchdog → app crash orphans it; non-n8n process on 45678 fails startup invisibly (§2 agents, §6) | `src-tauri/.../n8n.rs:646-700` | Add a parent-death watchdog that terminates n8n; detect and surface a foreign process on 45678. | M |
| **P2-5** | Gmail CRLF header injection | — | **Done in P1-9** (listed here for security completeness). | — |
| **P2-6** | Keychain / MCP posture verified clean (§6) | — | **No action.** Documented so we don't re-audit: 127.0.0.1 bind, restrictive CORS, MCP bearer + per-call scope, SHA-256 runtime downloads, no SQL injection. | — |

---

## 5. Phase 3 — Correctness under load

The §7.2 set: races and wedges that only bite under burst / local-model latency. Some depend on the status-transition SSOT (P7-4); noted.

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P3-1** | One hung LLM call wedges the whole pipeline; reaper can't run; resolution races → duplicate cards + double spend (§1.3) | `pipeline/queue.py:811` (`_consumer_loop`), reaper | Run the stale-event reaper as an **independent task**; have it cancel the matching in-flight task; bound the `asyncio.wait` batch. Ensure emit-time existing-card check is race-safe (ties to P3-8). | M |
| **P3-2** | Batch router drops the learning loop + index-misalignment + single-space assumption (§1.7) | `pipeline/router.py:100-116`, `build_batch_router_messages` | Pass `format_feedback_section()` + memory context **once per batch** (shared → cheaper than N single calls); add an echoed `event_index` to the batch schema and match on it (not array index); group batches by `space_id`. | M |
| **P3-3** | Entity-workspace resume: manual "Run Agent" spawns a duplicate after first run (§1.9) | `api/cards_api.py:2211` (`run_entity_agent`), `pipeline/processing_rules.py:411` | Call `get_session_for_entity(..., include_terminal=True)` and resume `completed`/`paused` alike, mirroring the processing-rule path. Add a **per-entity lock** (both paths are check-then-act). | M |
| **P3-4** | CLI agent sessions deadlock on >64KiB stream-json lines; idle timeout SIGKILLs paused sessions (§1.10) | `agents/subprocess_helper.py:61` | Spawn with `limit=10*1024*1024`; don't `except: break` silently — drain and log. Suspend the 300s idle clock while a session is paused (SIGSTOP produces no output). | M |
| **P3-5** | Transient LLM outage → permanent junk fallback cards (stager swallows transport errors) (§2 pipeline) | `pipeline/stager.py:102-104` | Let **transport** errors propagate (so the event retries, like the router already does); reserve `_build_fallback_card` for **parse** failures only. | S |
| **P3-6** | Debounce cancel kills in-flight summary runs → cards lost from forward-only summaries; summarize.py lost-update race (§2 pipeline, §3.4) | `pipeline/group_summary.py:62-64`, `pipeline/summarize.py:77-83` | Don't cancel a task that has already popped its batch (guard with an "in-flight" flag or catch `CancelledError` and re-enqueue popped ids). Add a **per-space lock** in `summarize.py` so a 90s flush can't overlap a running fold. | M |
| **P3-7** | Processing-rule `_exec_run_agent` sets `agent_running` via raw UPDATE before the session exists; spawn failure strands card (§2 pipeline) | `pipeline/processing_rules.py:445-489` | Route through `transition_card_status`; only transition after the session is confirmed created; on spawn failure revert. (Depends on P7-4 SSOT.) | S–M |
| **P3-8** | Status-transition races: `transition_card_status` is validate-then-write with no `AND status=?` guard; agent streaming paths use raw UPDATEs → dismissed card resurrected (§2 API, §2 agents) | `db` status helper, agent streaming write paths | Make the transition atomic: `UPDATE … WHERE id=? AND status IN (<valid froms>)`, check `rowcount`. Route agent-streaming status writes through the same helper. **Prereq for P3-7, P4-egress idempotency.** | M |
| **P3-9** | Executor: unexpected exception after `executing` transition strands card until startup sweep (§2 pipeline) | `pipeline/executor.py:87-149` | Wrap the execution body; on unexpected exception transition to `failed` with reason. | S |

---

## 6. Phase 4 — Functional bug sweep

Everything else in §2, grouped by domain. Independent — parallelizable across contributors.

### 6.1 Pipeline & post-emit

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P4-1** | Retries re-pay the router (in-memory cache misses persisted output) (§2, §3.2) | `pipeline/queue.py:264-273` | On `processing_attempts > 1`, reuse the persisted `router_output` on the event row. Pure LLM savings. | S |
| **P4-2** | Empty `subject.id` collapses unrelated events into one `entity_id` (§2) | `models/event.py:64-70` | When `subject.id` is null/empty, fall back to `event_id` so degenerate events don't share `gmail:email_thread:`. | S |
| **P4-3** | `entities` table grows unbounded dup rows per recurrence of same A↔B pair (§2, §5.7) | `pipeline/entity_resolution.py:97-117` | Add a unique constraint on the semantic-link pair (migration 072) + `INSERT … ON CONFLICT DO NOTHING`. Dedupe existing rows in the same migration (this table is load-bearing → backfill justified). | M |
| **P4-4** | Trace seeds stuff a refs-CSV into `entity_id`, breaking dedup/feedback-exclusion (§2, §4 N+1) | `pipeline/trace.py:577-585` | Use the real `entity_id` from ChromaDB metadata (available at embed time) instead of the refs CSV. | S |
| **P4-5** | Stager per-action parse uses bare key access → one malformed action burns 3 retries → dead-letter (§2) | `pipeline/stager.py:175-183` | Wrap each action parse; drop malformed actions, keep the good ones. | S |
| **P4-6** | Omni `_append_to_recent` mutates the shared cached snapshot before commit (§2, §4) | `pipeline/omni.py:382-526` | Deep-copy on cache hit; install into `_latest_cache` only **after** commit succeeds. | S |
| **P4-7** | `load_settings` leaks mutable refs to `DEFAULT_SETTINGS`; `save_settings(load_settings())` freezes defaults into the user file (§2, §4) | `config.py:218-247` | Deep-copy defaults on load; persist only the delta from defaults so future default changes still reach existing installs. (Pairs with P5-3 mtime cache.) | S–M |
| **P4-8** | Learned classification rules grow router prompt unboundedly (no cap, no consolidation) (§2, §3, §5.9) | `pipeline/feedback.py:77-92`, `pipeline/learn.py:152-158` | Port the context-rules protections: `max_injection` cap + LLM consolidation pass at a threshold. (Structurally converges with `context_learn.py` under P7-9.) | M |

### 6.2 API / DB

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P4-9** ◑ | `/cards/grouped` unbounded — no LIMIT, ships heavy JSON per card, UI drops `date` filter in bookmarked/related/all-days modes → multi-MB, blocks shared DB conn (§2, §4) | `api/cards_api.py`, feed caller | ✓ *Part a (payload slim)*: list drops `staged_output`/`suggested_actions` (`_row_to_card(slim=True)`), lazy-loaded on detail via `GET /cards/{id}`; `intelligence` kept for client-search parity; 3 grouped-card UI consumers fixed. Remaining *part b*: cap distinct groups server-side (`limit`/`offset` + `has_more`) + feed load-more. | M |
| **P4-10** ✓ | Feed search bypasses FTS5 — 16 `LIKE`s + correlated tag subquery per row per term (§2, §4) | `api/cards_api.py:271-296` | **Perf-only (user chose to keep substring matching over FTS token/stemming):** de-correlated the tag subquery into one hoisted `LEFT JOIN … GROUP BY` (`ctag.tag_names`); dropped the giant `staged_output`/`suggested_actions` JSON blobs from the scan. Substring `LIKE` semantics unchanged. `TestFeedSearch` covers it. | M |
| **P4-11** | WS broadcast prunes wrong connections (zips mutated list vs pre-await snapshot) → clients connecting mid-broadcast dropped (§2) | `api/websocket.py:36-44` | Zip against the **snapshot**; make `disconnect` idempotent/tolerant. | S |
| **P4-12** ✓ | Single shared aiosqlite conn → zero txn isolation; no `rollback()` anywhere; false "ONE transaction" comment (§2) | `db/*`, `_persist_card` | ✓ `db/sqlite.transaction()` = module `asyncio.Lock` (serializes guarded invariants against each other) + commit/rollback. Applied to space delete, card cascade, merge, both unlinks — the low-freq, off-hot-path invariants. Per plan, did **not** attempt per-request isolation (needs a conn pool) and left the hot `_persist_card` emit path unguarded (serializing it needs load testing). `test_db_transaction.py` covers commit/rollback + cascade. | M |
| **P4-13** | Migrations non-atomic → mid-file failure commits half the DDL, boot-loops next start (§2) | `db/migrate.py:52-59` | Wrap each migration file in a transaction; record `schema_version` only on full success. | S–M |
| **P4-14** | `_polishing` flag persisted with no startup sweep → restart mid-polish 409s forever + eternal spinner (§2) | `api/cards_api.py:1359` | Clear stale `_polishing` flags on startup (like the executor sweep). | S |
| **P4-15** | `dismiss_group` doesn't handle `ctx_` ids (asymmetric with `mark_group_read`) (§2) | `api/cards_api.py` | Handle `ctx_`-prefixed ids symmetrically. Latent — fix before next UI wire-up. | S |
| **P4-16** | Prev/next date nav applies *today's* UTC offset to historical dates (DST bug) + two full-table `DATE()` scans per feed load (§2, §4) | `api/cards_api.py:574-595` | Compute offset per-target-date; index/bound the date scans. Ties to timezone invariant. | S–M |
| **P4-17** | Card hard-delete orphans `tag_assignments` + `group_summaries.card_ids` (§2) | `api/cards_api.py:985-1003` | Cascade delete tag assignments; remove the id from `group_summaries.card_ids`. | S |

### 6.3 Egress

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P4-18** | SMTP dead end-to-end: `_load_credentials` reads `smtp:default`, creds stored under `smtp:{connection_id}`; also hardcodes STARTTLS (breaks port-465) (§2) | `egress/connections.py:502`, SMTP backend | Read the keychain key by `connection_id`; select STARTTLS vs SSL by port/config. Drive a real send to verify. | S–M |
| **P4-19** | `validate_payload` never called by any execution path — ~250 lines of adapter validation dead (§2) | `egress/registry.py` / `enrich_payload_from_event` | Call `validate_payload` once in `enrich_payload_from_event` (covers card, composer, chat paths). | S |
| **P4-20** | n8n executor timeouts double-send (runs to completion after 30s POST timeout, marked retryable, retry re-executes) (§2) | egress executor / n8n backend | Treat POST timeout as **outcome-unknown** (not retryable-delete-and-resend); use the stable `egr_{card_id}` action_id to dedupe in the workflows. | M |
| **P4-21** | gmail→`google_calendar` remap 404s on every cloned-workflow install (§2) | `egress/executor.py:96-100`, `egress/router.py:99-101` | Remap to platform `calendar` (resolves via sources), not the hyphenated default path. | S |
| **P4-22** | Chat-driven egress never sets `connection_id` (uses oldest) + never sets `jira_base_url` (falls back to literal `your-domain.atlassian.net`); composer's named-but-unmatched raise only fires when `executor_rows` non-empty (§2) | `egress/tools.py`, `tool_handlers.py`, composer path | Thread `connection_id` + `jira_base_url` through the chat egress path; make the composer's unmatched-connection raise fire even when `executor_rows` is empty. Reuses the wrong-account fix pattern. | M |
| **P4-23** | OAuth health never flags dead connections (refresh failure keeps `status=connected`, bumps `last_validated_at`); `_update_n8n_oauth_token` no-ops against a nonexistent n8n endpoint (§2) | `egress/health.py:95-97`, oauth token sync | On refresh failure set `status` to a degraded state and don't bump `last_validated_at`; fix or remove the n8n token-sync no-op (decide single source of refresh to avoid rotation drift on Microsoft). | M |
| **P4-24** | Execution idempotency is non-atomic check-then-act; `/egress/execute` (composer) has no dedup (§2) | egress execute paths | Make the `executing` transition atomic (`UPDATE … WHERE status IN (...)`, check rowcount) — shares mechanism with P3-8. Add composer dedup. | M |
| **P4-25** | `handle_callback` doesn't wrap workflow cloning in try/except → mid-clone exception leaves tokens + active clones with no connection row (§2) | `egress/oauth.py:339-345` | Wrap the clone; on failure roll back keychain tokens + any partial clones so Settings stays consistent. | S–M |

### 6.4 Agents / MCP / Tauri shell

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P4-26** | WS agent input is a silent no-op (stdin `DEVNULL`); UI records input as delivered (§2) | `agents/ws_router.py:64,81,99` | Delete/redirect the dead `send_input` handlers to the real `resume_with_answer` path. | S |
| **P4-27** | Resume paths have no running-state guard + drop reverse mappings → double subprocess; resumed sessions escape `cancel_sessions_for_card` (§2) | `agents/session_manager.py:468`, `api/workspace_api.py:186,360` | Guard resume against already-running sessions; re-register reverse (card→session) mappings on resume so cancel-on-archive works. | M |
| **P4-28** | MCP Streamable sessions leak a task+transport per abandoned `initialize`; `_stderr_lines` grows unbounded (§2, §4) | MCP server, subprocess reader | Reap idle/abandoned Streamable sessions; make `_stderr_lines` a bounded `deque`. | S–M |

### 6.5 UI

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P4-29** | Every search keystroke triggers a full backend refetch (`searchQuery` read before first await → tracked dep) → ~11 reloads for "hello world", defeats 300ms debounce (§2) | `ui/.../feed/+page.svelte:487,591` | Wrap the pre-await `searchQuery` parse in `untrack()`. | S |
| **P4-30** | No resync after WebSocket reconnect → feed silently stale after engine restart (§2) | `ui/.../feed/+page.svelte`, ws store | Watch `wsStatus`; on `disconnected→connected` call `scheduleReload()`. | S |
| **P4-31** | Card mutations fail completely silently (try/finally, no catch) across ActionCard/CardDetail/ListRow; 4 components hardcode optimistic reopen statuses that contradict backend restore logic (§2) | `ui/.../components/feed/*` | Add `catch` → toast/error state + revert optimistic change. Replace hardcoded `'pending'`/`'ready'` optimistic reopen with the backend's returned status. | S–M |
| **P4-32** | Omni page: errors after first load can't render (`{#if error && !snapshot}`); `omni_updated` ignores `space_id` and yanks users off historical snapshots; space-switch has no stale-response guard (§2) | `ui/.../omni/+page.svelte` | Ungate the error render; filter `omni_updated` by current `space_id` and current-view; add a fetch-id/stale-response guard on space switch. | M |
| **P4-33** | WS receive loop blocks for the whole streamed chat → approve/deny/cancel queue unprocessed; can't cancel a slow local-model chat (§2) | `main.py:438-441` (WS handler) | Dispatch `handle_ws_message` as a task so the receive loop stays responsive. | S |
| **P4-34** | Day Summary modal fires 2–3 duplicate fetches per open; CardDetail refetches related/egress on every WS status tick of the selected card (§2) | Day Summary modal, CardDetail | Collapse the overlapping triggers to one; key the CardDetail refetch on card **id/content** change, not prop identity. | S–M |
| **P4-35** | Coherence page: rerun lacks concurrent-trace guard; WS latch gap spuriously shows "New events detected" rerun banner; generation errors only `console.error` (§2) | `ui/.../coherence/+page.svelte` | Add a concurrent-rerun guard; close the WS latch gap; surface generation errors in the UI. | S–M |
| **P4-36** | Single-key shortcuts ignore open modals; `closeWebSocket` re-arms a zombie reconnect; `WsMessage` union omits 5 handled types (§2) | shortcut handler, ws store, `ui/.../types.ts` | Gate single-key shortcuts when a modal is open; make `closeWebSocket` cancel its reconnect timer; add the 5 missing message types to the `WsMessage` union. (`sub_groups` dead-code removal is P1-12.) | S |

---

## 7. Phase 5 — Performance & event-loop

| ID | Finding (ref) | Files | Fix | Effort |
|---|---|---|---|---|
| **P5-1** | `embed_document`/`delete_document` block the loop | — | **Done in P1-8.** | — |
| **P5-2** | Sync keychain reads on the loop, uncached — every Jira exec, n8n API call, webhook-cache refresh, health sweep does a blocking `keyring` call (§4) | `egress/connections.py`, `egress/backends/n8n.py:269`, `egress/oauth.py:132` | Add a small **TTL cache** (invalidate on store/delete) and/or `run_in_executor`. Removes the only blocking IO in the egress hot path. | M |
| **P5-3** | `load_settings()` re-reads + re-merges `settings.json` ~10×/event (§4) | `config.py` | mtime-keyed cache (also fixes the mutable-defaults leak — pairs with P4-7). | S |
| **P5-4** | FTS churn is O(N²) per thread — carry-forward `UPDATE … WHERE entity_id=?` rewrites every sibling; `cards_fts_au` is unconditional AFTER UPDATE (§4) | `pipeline/emit.py:265-272`, `db/fts.py:106-111` | Add `AND group_active_at < ?` to the carry-forward update; scope the FTS trigger to the indexed columns only. | M |
| **P5-5** | Feed broadcast waits behind an inline LLM call (context grouping + entity resolution) → new cards appear seconds-to-tens-of-seconds late while holding a pipeline semaphore slot (§4) | `pipeline/emit.py:706-748` | Broadcast the new-card WS message right after embed; run grouping post-broadcast and emit a `card_updated` follow-up. | M |
| **P5-6** | N+1 / unbounded queries: `_find_linked_entities` nested per-ref `LIKE` full scans; fuzzy `find_contact` fetches every matching event before deduping to 20 (§4) | `pipeline/trace.py:951-993`, `llm/tools/contact_tools.py:101-116` | Batch/bound the scans; dedupe in SQL / `LIMIT` before hydrate. (prev/next date scans → P4-16.) | M |
| **P5-7** | Unbounded memory: `_stderr_lines`, MCP Streamable sessions, omni cache mutation | — | **Covered by P4-28 (stderr/MCP) + P4-6 (omni cache).** | — |
| **P5-8** | Chat history ordering has 1s resolution + no tiebreaker → user/assistant pair reorders; count-based 10-msg window re-injects a 65K answer for 10 turns (§4) | `pipeline/chat.py:294-303` | Add `rowid DESC` tiebreaker; make the history window **token-budgeted** (or truncate oversized turns) instead of count-based. | S–M |

---

## 8. Phase 6 — Token & LLM-cost program (investigation-first)

**Gate:** before cutting any prompt, capture current token sizes + a golden-output regression set for that stage (per working principle #4). Several items here overlap P1/P3 and are cross-referenced.

### 8.1 Calls removed outright (zero prompt growth)

| ID | Finding (ref) | Fix | Effort |
|---|---|---|---|
| **P6-1** | Wire `context_match` (§3.1) | **Done in P1-1.** | — |
| **P6-2** | Reuse persisted `router_output` on retries (§3.2) | **Done in P4-1.** | — |
| **P6-3** | Briefing check-before-generate (§3.3) | **Done in P1-6.** | — |
| **P6-4** | Fix debounce-cancel wasted generations (§3.4) | **Done in P3-6.** | — |
| **P6-5** | Skip the context-summary cascade when the entity summary headline/status is unchanged (string compare) (§3.5) | Short-circuit the nested context-group regen when the parent summary's headline+status came back identical. | S |
| **P6-6** | Filter tenacity retries to genuinely-retryable errors — deterministic 400s can burn 9–18 doomed HTTP calls/event across nested retry layers (§3.6) | `llm/client.py:844-857` | Retry only on transient (timeout/5xx/connection) errors; fail fast on 4xx. | S |
| **P6-7** | Share one ChromaDB search per event — router/stager/each worker independently embed-and-search the same `title+body[:300]` (2–4 identical searches) (§3.7, §4) | Compute the embedding+search once per event, pass the result down the pipeline. Embedding cost on the hot path. | M |

### 8.2 Token reductions (local-model-safe — all shrink or hold prompt size)

| ID | Finding (ref) | Fix | Effort |
|---|---|---|---|
| **P6-8** | Chat tool definitions ~8.4K tok, resent every turn + every tool-loop iteration (cap 20) — single largest cost; exceeds an 8K local window before the question (§3) | Split the toolset: always send ~16 read/card-write tools (~4K); gate rules/settings/egress groups behind a cheap intent check. Trim descriptions that restate param docs; move operator/field enums into `get_rule_options` output. | L |
| **P6-9** | Stager system prompt (~3.3K/event) ~45% conditionally irrelevant (email-phishing on Jira events, PR-lifecycle on emails, ~900 tok of role-context explanation) (§3) | Build 3–4 per-platform-family stager variants — each a stable cache prefix; non-email events save ~1.2–1.5K. Same pattern for router (~600 tok of Jira/Bitbucket guidance on Gmail/Slack). | M |
| **P6-10** | Router emits a 3–5-step `research_plan` even when `requires_research=false` (§3) | Require an empty `research_plan` when `requires_research=false`. Dominant output cost for bot-noise on small models. | S |
| **P6-11** | Prompt caching half-wired: only tools+system get an Anthropic cache breakpoint; `_inject_current_datetime` stamps seconds-granular timestamp per loop iteration, churning the prefix (§3) | Freeze the timestamp per request; add a second cache breakpoint on the final message. Converts ~90% of a 20-iteration tool loop's input to 0.1× cache reads. | S–M |
| **P6-12** | Tool results appended uncapped (`limit` up to 200 with full `intelligence`/`content_body`) can blow a local context (§3) | Cap serialized tool results at ~8–16K chars with a "truncated, use offset" suffix. | S |
| **P6-13** | Omni resynthesis most truncation-prone (full snapshot + up to 150 cards ≈ 8–15K tok; failure grows the next attempt) (§3, §4) | Fold in chunks of ~30–50 across sequential smaller calls. More calls, each strictly smaller — right trade for local models. | M |
| **P6-14** | Ambient chat retrieval overlaps the tool loop — every turn (incl. "thanks") pays embedding + ChromaDB + 3 SQL + up to 3K injected tokens the model re-fetches (§3) | Shrink the ambient budget; skip it when the previous assistant turn used tools. | S–M |

### 8.3 Accounting bugs that hide the waste

| ID | Finding (ref) | Fix | Effort |
|---|---|---|---|
| **P6-15** | Truncation-doubling retry **overwrites** output tokens instead of adding (§3) | `llm/client.py:926` | Accumulate output tokens across the doubling retry. | S |
| **P6-16** | `get_current_month_cost` filters `success=1` → JSON-failure loops (Gemma spiral) invisible to the budget cap (§3) | Count failed attempts' token cost toward the cap. | S |
| **P6-17** | Streaming logs chars/4 estimates that exclude the entire tool block (§3) | Use `stream_options.include_usage` for real streaming usage. | S |

> P6-8/P6-9 are numbered separately but should share one "prompt-audit + cache-prefix" investigation pass.

---

## 9. Phase 7 — Refactor & de-duplication program

**Strict order** (§7.4): retrieval → persona workers → agent adapters → llm_call/streaming → cards_api split → feed page. Each refactor is behavior-preserving with the existing tests as the guard; land the relevant bug fixes from earlier phases *first* only where they're one-liners, otherwise fix-during-refactor to avoid touching the same code twice (noted).

| ID | Finding (ref) | Scope | Effort |
|---|---|---|---|
| **P7-1** | Three parallel retrieval stacks — stopwords ×4, RRF duplicated, "FTS-else-LIKE" ×5 with divergent semantics (`search_cards` OR on FTS path but AND on LIKE path, contradicting its own description) (§5.3) | Extract one `retrieval.py` (~400 lines removed). Fixes the AND/OR divergence. **Do before P4-10/P6-7** (shared retrieval code). | L |
| **P7-2** | Five persona workers near-verbatim (~250 lines); drift: engineer takes `space_id` others don't; ops/finance drop `participant_roles` (§5.1) | Replace with one `run_persona_worker(spec)` + per-persona table; unify the identity-block builder + `_summarize_findings` (triplicated). | L |
| **P7-3** | Four CLI agent adapters duplicate ~380 lines; drift: only Claude handles `rate_limit_event`/truncation (§5.2, §5.10) | Move lifecycle into `base.py`; adapters keep only spawn-args + line parsing. Fixes the drift so all tiers get rate-limit + truncation handling. | L |
| **P7-4** | Status-transition SSOT — `cards_api.reopen_card` bypasses lifecycle with 4 duplicated update/broadcast blocks; agent paths raw-UPDATE (§5.4) | Consolidate all status writes behind `transition_card_status`. **Prereq for P3-7, P3-8, P4-24.** Do this early despite its P7 number. | M |
| **P7-5** | `llm_call_streaming` is a drifted ~200-line copy of `llm_call` — missing max_tokens clamp (sends 65,536 to strict vLLM → the exact 400 the clamp was built for), real usage accounting, agent-model guard (§5.6) | Extract shared `_prepare_call_kwargs()`. **Do before/with P6-11/P6-17** (streaming path). | M |
| **P7-6** | `cards_api.py` (2,800 lines) splits along 6 seams; 30-column card SELECT duplicated 4× and drifted (trace_api omits `read_at`/`context_id`) (§5.4) | Split into feed/grouping, lifecycle, action-payload/polish, agent-run, context-groups, group-summaries modules; single canonical card-SELECT. | L |
| **P7-7** | `feed/+page.svelte` (2,617 lines) (§5.5) | Extract: WS reducer (642-873 → pure, unit-testable — most bug-dense), FLIP/layout engine (×3 copy), filter popover, summary modal, drawer. Unify badge/status/priority maps (copy-pasted ~9× with divergent values) + `timeAgo` (~15 files). | L |
| **P7-8** ◑ | `learn.py` / `context_learn.py` structurally near-identical; tuning defaults duplicated (`DEFAULT_SETTINGS["tuning"]` vs inline `get_tuning` literals) (§5.9) | ✓ Tuning defaults centralized (DEFAULT_SETTINGS is SSOT); ✓ shared `get_spaces_with_unprocessed` extracted to `learn_common.py`. Deferred: full extraction-loop driver + porting consolidation to the classification side (P4-8). | M |
| **P7-9** | Dead code with a caveat: `oauth._setup_n8n_workflows` (157 lines, holds the 3rd copy of the credential-injection matcher "must stay in sync") (§5.7) | Remove after confirming the live matcher copies are canonical; collapse to one matcher. | M |
| **P7-10** | Rust shell ~250 lines dup'd across `sidecar/n8n/runtime.rs` (4× SIGTERM→poll→SIGKILL, ×3 `home_dir`, ×2 env sanitizers) (§5.8) | Collapse into one `process_util.rs`. Pairs with P2-3/P2-4 (process handling). | M |

Agent-tier rough edges (§5.10) — schema-retry references "your previous response" though each attempt is a fresh process; `max_tokens`/`temperature` ignored; truncation invisible for non-Claude; `_strip_think_blocks`/`_extract_json` duplicated between `client.py` and `agent_backend.py` — are folded into **P7-3** + a shared `_extract_json` extraction.

---

## 10. Explicitly *not* changing (verified good — §8)

Do not "improve" these; they are load-bearing decisions:

- Durable event queue (INSERT OR IGNORE idempotency, atomic claims, backoff, dead-lettering, startup recovery).
- Omni crash-safe durable queue (enqueued in the card-insert transaction, zero inline LLM).
- Daily-summary batching (cap 10/call) + thread-context reuse of group summaries.
- Persona gating on `requires_research`; datetime injected in the **user** message (cache-safe by design); omni `maxItems` schema bound.
- FTS5 escaping + LIKE fallback + complete trigger coverage (no `INSERT OR REPLACE`).
- Padding/truncation salvage ladder scoped to custom providers.
- Batch-routing circuit breaker + local-provider skip; processing-rules defensive layers.
- Egress `Platform` adapter layering (preview/execute can't drift); version-gated n8n sync; `_resolve_webhook_url` wrong-account fix.
- `engine.ts` single `request()`; feed hybrid incremental-patch/full-reload WS strategy + FLIP; full listener/timer cleanup on destroy.

---

## 11. Suggested first sprint (concrete starting set)

All of **P1** (12 tasks, all ≤ S/M) + **P2-1** (TrustedHost) + **P3-8/P7-4** (status SSOT + atomic transitions, since so many later tasks depend on them). This clears every §7.1 quick win, closes the two cheapest security holes, and unblocks the correctness-under-load and egress-idempotency work.

---

## 12. Coverage matrix

Every review subsection → task IDs. (Items marked ✓ are fully covered; none are dropped.)

| Review section | Tasks |
|---|---|
| §1.1 context_match | P1-1 ✓ |
| §1.2 ghost embeddings | P1-2, P1-8 ✓ |
| §1.3 pipeline wedge | P3-1 ✓ |
| §1.4 reorder route | P1-3 ✓ |
| §1.5 n8n password | P1-4 ✓ |
| §1.6 phantom budget | P1-5 ✓ |
| §1.7 batch router | P3-2 ✓ |
| §1.8 scheduler / rollover | P1-7 ✓ |
| §1.9 workspace resume | P3-3 ✓ |
| §1.10 subprocess deadlock | P3-4 ✓ |
| §2 Pipeline & post-emit | P3-5, P3-6, P3-7, P3-9, P4-1…P4-8 ✓ |
| §2 API/DB | P4-9…P4-17, P3-8 ✓ |
| §2 Egress | P1-9, P1-11, P4-18…P4-25 ✓ |
| §2 Agents/MCP/Tauri | P2-1…P2-4, P4-26…P4-28 ✓ |
| §2 UI | P4-29…P4-36, P1-12 ✓ |
| §3.1–3.7 calls removed | P1-1, P4-1, P1-6, P3-6, P6-5, P6-6, P6-7 ✓ |
| §3 token reductions | P6-8…P6-14 ✓ |
| §3 accounting bugs | P6-15, P6-16, P6-17 ✓ |
| §4 Performance | P1-8, P5-2…P5-8, P4-6, P4-16, P4-28 ✓ |
| §5.1–5.10 redundancy | P7-1…P7-10, P1-12 ✓ |
| §6 Security | P1-4, P1-9, P2-1…P2-4 ✓ |
| §7 priority order | reflected in §2 phase overview + §11 ✓ |
| §8 keep as-is | §10 ✓ |
