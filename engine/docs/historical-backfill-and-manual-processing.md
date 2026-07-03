# Historical Backfill & Deferred (Manual) AI Processing

**Status:** Design — pending implementation
**Date:** 2026-06-20
**Author:** design session (Aayush + Claude)

## 1. Motivation & positioning

Today Laya only sees events from the moment a platform is connected, forward. The pipeline
also couples two things that should be independent: **a card existing** and **a card having
been AI-processed**. A card row only comes into being *after* the router + stager have run
(`emit.py`), and the ChromaDB document is built from AI output and keyed by `card_id`.

Consequences:

- **Day-0 emptiness.** A fresh install has zero history, so for a "smart inbox" user (not a
  power-user aggregator) Laya looks useless until events accumulate.
- **All-or-nothing cost.** Every ingested event runs the full LLM pipeline; there is no way to
  capture-and-defer.

This feature repositions Laya as *also* a smart inbox by introducing two capabilities that
share one underlying mechanism:

1. **Historical backfill** — pull the last *X* days (and, for calendars, a small future window)
   of events from Gmail / Outlook Mail / Google Calendar / Outlook Calendar, capture and index
   them for search, but **do not** run the expensive AI pipeline on them.
2. **Manual (deferred) AI mode** — a per-space option (with a global default) to ingest + index
   events without auto-running the AI pipeline, letting the user trigger enrichment per-card or
   in bulk.

## 2. Core concept: decouple *capture* from *enrichment*

Treat AI enrichment as an **optional layer on top of a captured event**, not a precondition for a
card existing.

```
  CAPTURE (base layer)                 ENRICH (optional layer)
  ─────────────────────                ───────────────────────
  event stored                         router classification
  raw card created (ai_skipped)   ──▶  persona worker + stager
  embedded in ChromaDB (raw text)      intelligence report / staged output
  searchable, browsable                entity resolution, grouping, summaries, omni
```

Backfill, manual-mode live events, and (conceptually) failed cards are all the **same state**: a
card that exists but has not been enriched. They flow through **one new pipeline branch** and are
later promoted to full cards through the **existing reprocessing machinery**.

## 3. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Backfill execution | **Engine-native fetch** — Python calls provider APIs directly using OAuth tokens already in the keychain. (n8n cannot be triggered for one-off runs today.) |
| 2 | Where raw cards surface | **Feed, bucketed by original event date.** Hidden from the default view behind a "Show unprocessed" toggle. |
| 3 | Auto/manual mode granularity | **Per-space, with a global default.** |
| 4 | Calendar backfill direction | **Past window + a small bounded future window** (upcoming already-scheduled meetings). |

## 4. Why the naive "just embed it" plan needs one addition

The embedding path is in `emit.py:38-72` (`_build_embedding_text`), **not** `trace.py`
(`trace.py` is the read/search side). Today the embedded text is built entirely from AI output
(`header`/`summary` from the stager; `category`/`entity_refs` from the router) and the document
id is the `card_id`. There is **no path that embeds a raw event**.

The *embedding model itself* (local `nomic-embed-text`) is free compute — the cost is in
generating the AI text, not embedding it. So we add a **raw embedding text builder** that embeds
straight from the event (subject, body snippet, sender, platform). This keeps the "no LLM cost"
promise while producing genuinely searchable vectors on day 0.

## 5. Data model changes

### 5.1 New card status: `ai_skipped`

`action_cards.status` is free-text TEXT, but the lifecycle is governed by
`models/card_lifecycle.py`. Add `ai_skipped`, distinct from `failed` (which carries
`failed_stage` + `last_error` + retry semantics):

```python
VALID_STATUS_TRANSITIONS = {
    ...
    "ai_skipped":  {"pending", "dismissed", "archived"},   # NEW — non-terminal
    "pending":     {..., "ai_skipped"},                    # raw path may land here first
    ...
}
# ai_skipped is NOT in TERMINAL_STATUSES
```

`ai_skipped` ≠ `failed`:
- `failed` = AI processing was attempted and errored (network/rate-limit); has `failed_stage`,
  `last_error`; recovered via the existing reopen/retry flow.
- `ai_skipped` = AI processing was **intentionally not run**; promoted via an explicit
  "Process with AI" action.

### 5.2 Raw cards need values for the five `NOT NULL` columns

`priority, persona, category, header, summary` are `NOT NULL`; `intelligence, staged_output,
suggested_actions, confidence` are nullable. The raw path fills:

| Column | Raw value |
|--------|-----------|
| `header` | `event.subject.title` (e.g. email subject) |
| `summary` | truncated `event.content.body` snippet |
| `priority` / `persona` / `category` | sentinel `"NONE"` (never displayed — UI keys off `status === 'ai_skipped'`) |
| `intelligence` / `staged_output` / `suggested_actions` / `confidence` | `NULL` |
| `entity_id` | deterministic `platform:subject_type:subject_id` (incl. the Gmail thread normalization at `emit.py:165-194`) so native email threads still group for free |
| `source_ref` / `source_url` | from `event.subject` via `format_source_ref` (same as `emit.py:200-206`) |
| `group_active_at` | **`event.timestamp`** (the *original* time, NOT `now()`) — this is what buckets raw cards under their real historical dates in the feed |
| `space_id` | resolved space |

**Action item:** confirm `CardResponse` (`models/card.py:63-102`) types `priority/persona/category`
as `str`, not the classification enums, so a `"NONE"` sentinel deserializes. If it uses the enums,
relax to `str` (the UI already hides these chips for raw cards).

### 5.3 `events.skip_ai` column (migration)

Add `skip_ai BOOLEAN DEFAULT 0` to `events`. Set by backfill (always `1`). The queue computes the
effective skip decision as:

```
skip_ai = events.skip_ai OR (resolved_space.ai_mode == "manual")
```

### 5.4 `spaces.ai_mode` column (migration)

Add `ai_mode TEXT DEFAULT 'auto'` (`'auto'` | `'manual'`) to `spaces`. `NULL`/absent means inherit
the global default in `settings.json` (`ai_processing.mode`, default `'auto'`). Read during
`resolve_space` (already runs early in `process_event`).

### 5.5 `backfill_jobs` table (migration)

```sql
CREATE TABLE backfill_jobs (
    job_id          TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL,
    platform        TEXT NOT NULL,
    space_id        TEXT NOT NULL,
    direction       TEXT NOT NULL,         -- 'past' | 'future' | 'both'
    since_ts        DATETIME,              -- window lower bound (UTC)
    until_ts        DATETIME,              -- window upper bound (UTC)
    status          TEXT NOT NULL,         -- running|paused|completed|failed|cancelled
    total_estimate  INTEGER,
    fetched_count   INTEGER DEFAULT 0,
    created_count   INTEGER DEFAULT 0,     -- cards actually created (post-dedup)
    cursor          TEXT,                  -- provider pageToken / @odata.nextLink / last ts
    error           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

A per-connection **low-water mark** (earliest timestamp already backfilled) lets a later
"extend window" run fetch only the gap rather than re-fetching. Derive it from the min `since_ts`
of completed jobs for that connection, or store it on the connection/source row.

## 6. Pipeline changes

### 6.1 New skip decision + raw branch in `queue.py`

In `process_event` (`queue.py:214-328`), after INGEST + SPACE RESOLUTION + RULES and **before**
the ROUTER LLM call:

```python
skip_ai = event_row.skip_ai or (resolved_space.ai_mode == "manual")
if skip_ai:
    await _run_raw_pipeline(event, space_id)     # NEW
    await _mark_completed(eid)
    return
# else: existing router → workers/simple → emit
```

`_run_raw_pipeline(event, space_id)`:
1. Insert the raw `action_cards` row (status `ai_skipped`, fields per §5.2).
2. Embed via the **raw** text builder (§6.2) → `embed_document(card_id, text, metadata)`.
3. **Skip all post-emit AI steps** (`entity_resolution`, `context_grouping`, `group_summary`,
   `omni`). Native-thread grouping via `entity_id` still works with no AI.
4. Broadcast `card_created` — but **batched/suppressed during backfill** (§6.5).
5. **Suppress user notifications** for raw cards (a 300-email import must not fire 300 pings).

### 6.2 Raw embedding text builder

Add `_build_raw_embedding_text(event)` mirroring the shape of `_build_embedding_text` but sourced
from the raw event:

```python
parts = [f"{platform} message: {event.subject.title}."]
parts.append(snippet(event.content.body))
if actor_name: parts.append(f"From: {actor_name}.")
parts.append(f"Identifiers: {source_ref}.")
```

Metadata mirrors `emit.py:418-429` but with `content_type="card_summary"`, `enriched=False`, and
`persona`/`priority` set to `"unknown"`. The `enriched` flag lets chat/trace retrieval distinguish
raw vs enriched docs.

### 6.3 Manual "Process with AI" — single & bulk

Reuses the existing reprocessing path: `queue.py` already routes a reprocessed event into its
**existing** card via `existing_card_id` (looked up by `event_id`), and Chroma `upsert` (keyed by
`card_id`) **replaces** the raw doc with the enriched one. No duplication.

- **Single:** `POST /cards/{card_id}/process`
  → `transition_card_status(ai_skipped → pending)`
  → reset the event (`processing_status='queued'`, `skip_ai=0`, `processing_attempts=0`)
  → queue runs the full pipeline; `emit` UPDATEs the card and re-embeds.
- **Bulk:** `POST /cards/process` `{ filter | card_ids }` with guardrails (§8): count/cost
  confirm, concurrency cap (reuse the queue semaphore, default 4), live progress, cancel,
  `budget.py` awareness.

**`is_historical` hint.** When the event timestamp is older than ~7 days, pass an `is_historical`
flag into the router/stager (and worker) prompts so they bias toward *summarize / inform* rather
than drafting a reply or proposing an action on a weeks-old thread.

**`group_active_at` on promotion.** `emit.py:305-309` currently slams `group_active_at = now()` for
the whole entity group. For a card promoted **from `ai_skipped`**, preserve the original
`group_active_at` (event time) so bulk-processing a historical inbox does not stampede everything
onto "today." (Single manual processing could optionally bubble-to-today; default is preserve.)

## 7. Backfill subsystem (engine-native)

New module `engine/laya/backfill/` :

```
backfill/
  __init__.py
  jobs.py          # job lifecycle: create, run, resume, cancel, progress
  runner.py        # background task; rate-limit, pagination, batching, dedup
  normalize.py     # provider payload → LayaEvent (event_id scheme MUST match live)
  providers/
    gmail.py       # users.messages.list q="after:.. before:.." label:INBOX, pageToken
    gcal.py        # events.list timeMin/timeMax, singleEvents=true (expand recurrence)
    outlook_mail.py# Graph /me/messages $filter=receivedDateTime ge/le, @odata.nextLink
    outlook_cal.py # Graph /me/calendarView startDateTime/endDateTime
```

Key points:

- **Auth reuse.** OAuth tokens live in the keychain (`oauth.py`, key `"{platform}:{connection_id}"`),
  with `refresh_access_token()` already implemented. The fetchers pull the token, refresh if
  expiring, and call the provider REST API directly.
- **`event_id` scheme MUST match live ingestion** (e.g. `evt_gmail_<id>`) — this is what makes
  `INSERT OR IGNORE` on the `events` PK dedup backfill against live events for free. Extract the
  exact id construction from each live n8n workflow's "Normalize" node and mirror it in
  `normalize.py`. *This is the single most important correctness detail.*
- **Shared ingest helper.** Factor `receive_event`'s store+enqueue logic (`api/events.py:35-102`)
  into `ingest_event(event, skip_ai=True)` used by both the webhook and backfill (direct
  in-process insert; avoids thousands of loopback HTTP calls).
- **Calendar windows.** Past window = `now - X days`; future window = `now + N days` (default
  N≈30) so already-scheduled upcoming meetings surface. `singleEvents=true` expands recurrence;
  handle cancellations/updates idempotently via the stable event id.
- **Resume / extend.** Persist the provider `cursor` on the job; resume continues from it. Track
  the per-connection low-water mark so "import 90 days" after a prior "30 days" fetches only 30–90.
- **Progress / cancel.** The runner updates `fetched_count`/`created_count` and emits a **single**
  throttled `backfill_progress` WS event (never per-card). Cancel sets `status='cancelled'`; the
  runner checks between pages.
- **Timezone.** The `since`/`until` cutoffs and provider date filters must respect UTC storage —
  do not convert a local date to UTC for the lookup (see `feedback_timezone_handling`). There is
  an existing `utc_cutoff(days)` precedent in `api/events.py:157-172`.

### 7.1 Backfill API

```
POST   /connections/{connection_id}/backfill   { days, future_days?, space_id? }  -> job
GET    /backfill/jobs                           -> [job ...]
GET    /backfill/jobs/{job_id}                  -> job (progress)
POST   /backfill/jobs/{job_id}/cancel
```

Only enabled for `gmail`, `google_calendar`, `outlook`, `outlook_calendar` in v1.

## 8. Edge cases & guardrails

| Concern | Handling |
|---------|----------|
| **Dedup on window overlap** | Free via `events` PK `INSERT OR IGNORE`, *iff* `event_id` scheme matches live. Verify per platform. |
| **Historical ordering** | `group_active_at = event.timestamp` on the raw card. |
| **Partial backfill failure** | Resumable from persisted `cursor`; job status, not fire-and-forget. |
| **Bulk-process cost blow-up** | N cards × 2–4+ LLM calls. Confirm dialog with count/est cost; concurrency cap; progress; cancel; `budget.py`; optional daily cap. |
| **WS flood** | Suppress per-card `card_created` during backfill; emit one throttled `backfill_progress`. |
| **Notifications** | Raw/skipped cards never notify. |
| **Calendar specifics** | Future window; `singleEvents=true`; cancellations/updates idempotent by id. |
| **Re-embed on promotion** | Chroma `upsert` by `card_id` replaces raw doc — already handled. |
| **Raw cards in chat/RAG** | Retrieval/context-packing must tolerate `intelligence/staged_output = NULL`; `enriched=False` metadata flags them. Audit `chat.py` packing. |
| **Briefings / omni / daily summary** | Exclude `ai_skipped` (they read header/summary/priority) until promoted. |
| **Feed default** | `ai_skipped` excluded from the default `getGroupedCards` result; revealed by a "Show unprocessed" toggle (mirrors `show_archived`). |
| **Sentinel enums** | `priority/persona/category = "NONE"`; confirm `CardResponse` uses `str`. UI hides chips for raw cards regardless. |
| **Promotion `group_active_at`** | Preserve original date for cards promoted from `ai_skipped` (avoid bulk stampede to "today"). |
| **Disconnect** | Backfilled raw cards are local data; they persist after a platform is disconnected (consistent with current behavior). |
| **Volume / device** | Cap window (e.g. ≤365 days); estimate count up front (Gmail `resultSizeEstimate`); warn above a threshold; embeddings batched in the background. |
| **Migration default** | All existing cards remain "processed" (no `ai_skipped`); new columns default to auto/0. |

## 9. UI changes

- **`ActionCard.svelte`** — when `status === 'ai_skipped'`: replace priority/persona chips with a
  neutral "Unprocessed" badge; show the body snippet as `summary`; primary action = **Process with
  AI**; keep archive/dismiss. (Color maps already have fallbacks, so no crash even before this.)
- **`CardDetail.svelte`** — surface the original content prominently (raw cards have no AI sections;
  the Intelligence Report block at `:738` already self-omits when empty). Add a "Process with AI"
  CTA.
- **Feed (`routes/feed/+page.svelte`)** — add a "Show unprocessed" toggle to `feedFilters`; when
  on, include `ai_skipped` in the `status` query param. Raw cards bucket under their original dates
  via `group_active_at`, so the default "today" view stays clean.
- **Audit tab (`AuditLogViewer.svelte`)** — new **"Awaiting AI"** section listing `ai_skipped`
  cards with per-card **Process** and a guarded **Process All** (alongside Dead Events / Ingestion
  Errors / Filtered Events).
- **Integrations (`PlatformCard.svelte`)** — for the four supported platforms, add a per-connection
  **"Import history"** control in the expanded action cluster (follow the existing Slack "Channels"
  inline-editor pattern at `:206-253`): a day picker (+ future-days for calendars) → starts a job →
  inline progress bar driven by `backfill_progress` WS events.
- **Settings (Spaces tab)** — per-space `ai_mode` (auto/manual) toggle; a global default in the
  general/processing settings.

## 10. Phased rollout

1. **Schema + lifecycle** — migrations (`events.skip_ai`, `spaces.ai_mode`, `backfill_jobs`);
   add `ai_skipped` to `card_lifecycle.py`; confirm/relax `CardResponse` types.
2. **Raw pipeline branch** — `_run_raw_pipeline`, `_build_raw_embedding_text`, the skip decision in
   `process_event`, notification + WS suppression, post-emit AI skip.
3. **Manual processing** — `POST /cards/{id}/process` + bulk endpoint with guardrails; `is_historical`
   hint; preserve-`group_active_at`-on-promotion.
4. **Manual mode (live)** — `spaces.ai_mode` + global default wired into the skip decision; Spaces UI.
5. **Backfill subsystem** — `backfill/` module, providers (Gmail first), jobs/runner, API,
   resume/cancel/progress.
6. **UI surfaces** — raw `ActionCard`/`CardDetail`, feed "Show unprocessed" toggle, Audit "Awaiting
   AI" section, Integrations "Import history" control + progress.
7. **Exclusions & retrieval audit** — exclude `ai_skipped` from briefing/omni/summary; verify chat
   retrieval tolerates raw docs.

Phases 1–3 deliver "manual processing of skipped cards" end-to-end and are independently testable
before any provider fetch code exists. Phase 5 (backfill) is the largest and can ship per-platform
(Gmail → Outlook Mail → Google Calendar → Outlook Calendar).

## 11. Open questions / future

- **Cost preview accuracy** for bulk processing — refine `budget.py` estimates per persona/path.
- **Slack/Jira/etc. backfill** — deliberately out of scope for v1 (pagination + rate-limit + less
  inbox-like). The raw-card mechanism is platform-agnostic, so they can be added later.
- **Auto-promote heuristics** — eventually, manual-mode could auto-promote cards the user *opens*
  or *searches into*, blending the two modes.
