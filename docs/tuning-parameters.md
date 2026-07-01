# Tuning Parameters

Laya exposes internal parameters that control the behavior, sensitivity, and resource usage of its AI pipeline. These parameters have sensible defaults and do not need to be changed for normal operation. Advanced users can override them via the `tuning` section in `~/.laya/settings.json`.

## How to Override

Add a `tuning` object to your `~/.laya/settings.json`. Only include the parameters you want to change — omitted parameters use their defaults.

```json
{
  "tuning": {
    "context_association_time_window_days": 14,
    "trace_semantic_max_distance": 0.50
  }
}
```

Changes take effect on the next pipeline run (no restart required).

---

## Context Association

Parameters that control how Laya detects semantic relationships between cards from different sources.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `context_association_time_window_days` | `7` | Only consider cards created within this many days for context grouping. Prevents old cards from matching new ones just because they have similar structure. **Lower** = fewer but more temporally relevant matches. **Higher** = broader search window, more potential matches but also more false positives. |

The distance thresholds for context association are in the `smart_grouping` section (not `tuning`):

| Setting | Default | Description |
|---------|---------|-------------|
| `smart_grouping.auto_confirm_threshold` | `0.12` | ChromaDB cosine distance below which two cards are auto-grouped without LLM confirmation. **Lower** = stricter, only near-identical content auto-groups. Range: 0.0 (identical) to ~0.5. |
| `smart_grouping.confidence_threshold` | `0.22` | Maximum cosine distance for LLM-confirmed grouping. Cards with distance between `auto_confirm_threshold` and this value are sent to the LLM for confirmation. Cards beyond this distance are never grouped. **Lower** = fewer candidates reach the LLM, fewer false positives but also fewer true positives. |

## Entity Resolution

| Parameter | Default | Description |
|-----------|---------|-------------|
| `semantic_entity_threshold` | `0.35` | Cosine distance threshold for semantic entity linking (Layer 2). Controls when extracted entities (ticket IDs, people, repos) are considered related across cards. **Lower** = stricter matching, fewer cross-references. |
| `entity_search_results` | `5` | Number of ChromaDB results to fetch per entity during semantic resolution. **Higher** = more thorough search but slower. |

## Classification Learning

Parameters that control how Laya learns classification rules from user corrections (priority/persona adjustments).

| Parameter | Default | Description |
|-----------|---------|-------------|
| `classification_learn_threshold` | `15` | Minimum unprocessed corrections per space before the learner triggers. **Lower** = learns from smaller batches (potentially less reliable rules). **Higher** = waits for more evidence but learns less frequently. |
| `classification_learn_batch` | `50` | Maximum corrections sent to the LLM in a single learning call. **Higher** = more context for rule extraction but larger prompt cost. |
| `classification_learn_interval_hours` | `6` | How often the scheduler checks for unprocessed corrections. **Lower** = faster learning loop. |

## Context Association Learning

Parameters that control how Laya learns from user link/unlink actions to improve context grouping.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `context_learn_threshold` | `10` | Minimum unprocessed context corrections per space before the learner triggers. |
| `context_learn_batch` | `40` | Maximum context corrections sent to the LLM in a single learning call. |
| `context_learn_interval_hours` | `6` | How often the scheduler checks for unprocessed context corrections. |
| `context_rules_max_injection` | `20` | Maximum learned context rules injected into the LLM confirmation prompt. **Higher** = more guidance for the LLM but larger prompt. |
| `context_corrections_max_injection` | `10` | Maximum recent user link/unlink actions injected into the confirmation prompt as examples. |
| `context_rules_consolidation_threshold` | `40` | When the number of **learned** context rules for a scope exceeds this, an LLM consolidator merges redundant rules. Manual rules are preserved untouched. |

## Trace / RAG Search

Parameters that control Laya's search for the Trace feature (deep research on cards). Trace and chat retrieval are **hybrid**: vector results (ChromaDB, bounded by the `*_semantic_max_distance` cutoffs below) are fused with lexical BM25 matches from SQLite FTS5 (`cards_fts` / `events_fts`) via Reciprocal Rank Fusion — so the distance cutoffs bound only the vector half, and exact-keyword hits surface even when their embedding distance is large.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trace_search_results` | `30` | Number of results fetched per search strategy (identifier, semantic, entity, text, fuzzy). **Higher** = more thorough but slower traces. |
| `trace_max_seeds` | `20` | Maximum seed results retained after merging all search strategies. Controls how many starting points the trace uses for deep research. |
| `trace_semantic_max_distance` | `0.65` | Maximum cosine distance for semantic search in traces. **Higher** = broader but noisier results. **Lower** = fewer but more relevant results. |

## Chat Retrieval

Parameters that control how much context Laya's chat retrieves when answering questions.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chat_semantic_max_distance` | `0.60` | Maximum cosine distance for semantic search in chat context retrieval. **Lower** = more relevant but fewer results. |
| `chat_context_items` | `12` | Maximum context items (cards, events, entities) included in the chat prompt. **Higher** = more context for the LLM but larger prompt cost. |

## Router

| Parameter | Default | Description |
|-----------|---------|-------------|
| `router_related_context_results` | `3` | Number of related past cards fetched from ChromaDB to provide context during event classification. Helps the router make consistent decisions based on how similar events were classified. |

## Feedback

| Parameter | Default | Description |
|-----------|---------|-------------|
| `feedback_time_window_days` | `30` | Time window for querying user feedback patterns (approval/dismissal rates). Injected into the router prompt to help it learn from user behavior. **Higher** = longer history, more data. **Lower** = focuses on recent behavior. |

## Corrections Cleanup

| Parameter | Default | Description |
|-----------|---------|-------------|
| `corrections_retention_days` | `30` | Days to keep processed corrections before automatic deletion. Applies to both classification and context corrections. Unprocessed corrections are never deleted. |

## Processing Rules

Parameters governing the automated processing-rules engine (Settings → Rules). These live outside `tuning` in settings.json.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `processing_rules.auto_disable_threshold` | `5` | Number of **consecutive** errors that auto-disables a rule. The counter resets to `0` on any successful firing and whenever the rule is re-enabled. Clamped to 1–100 by the settings API. **Lower** = disables flaky rules sooner. |
| `retention.firing_log_retention_days` | `90` | Days to keep processing-rule firing-log entries (the cross-rule Activity log) before the scheduler prunes them. Lives in the `retention` section alongside the other `*_retention_days` keys. |

## Agent Inference Backends & Usage Budget

Apply when a pipeline stage runs on an installed CLI agent (model id `agent/<id>/<model>`) instead of an API model. The window-based usage budget lives under `agent_budgets` in settings.json — separate from the monthly `$` budget — and auto-pauses ingestion before an agent's rolling quota is exhausted, auto-resuming at the window reset.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `agent_backend_concurrency` | `3` | Maximum agent subprocesses run concurrently across the pipeline. Floored at 1. **Higher** = more throughput but more local CPU/RAM and faster quota burn. |
| `coding_agent` | `"claude_code"` | Which installed CLI agent the workspace feature drives. One of `claude_code`, `gemini_cli`, `codex_cli`, `pi_cli`. |
| `agent_paths.<id>` | `""` | Explicit path to an agent's CLI binary (keys: `claude_code`, `gemini_cli`, `codex_cli`, `pi_cli`). Empty = auto-detect on `PATH` (augmented with common install dirs like `/opt/homebrew/bin`, `~/.local/bin`). |
| `agent_budgets.enabled` | `false` | Master switch for window-based agent usage budgeting. |
| `agent_budgets.agents.<id>.window_token_limit` | `0` | Token budget per rolling window for that agent (`0` = no limit). |
| `agent_budgets.agents.<id>.window_hours` | `5.0` | Rolling window length in hours (Claude Code's quota window is ~5 h). |
| `agent_budgets.agents.<id>.pause_at_percent` | `85` | Pause ingestion when window usage reaches this percent of the limit. Auto-resumes at the next window reset; a native rate-limit signal from the agent can also trigger the pause. |

## LLM Calls

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pipeline.model_timeout` | `480` | Per-call timeout in seconds for any LLM / agent call (settings.json `pipeline` section). Agents and verbose local models are slower than hosted APIs, so this is deliberately generous. |

`max_tokens` defaults to a lenient `65536`. It is a *ceiling*, not a target — well-behaved models stop at `finish_reason=stop`, so the high cap costs nothing. Laya auto-clamps the request down to each model's advertised output ceiling so the default never trips a `400` on strict providers (vLLM / OpenAI / Anthropic), while still preventing mid-document truncation of structured JSON on verbose local models (Gemma 3, LM Studio).

## Pipeline Debounce & Batching

Parameters that control how Laya batches and debounces LLM calls for efficiency. These reduce the number of LLM calls during burst activity without affecting card quality. Located under `pipeline.debounce` in settings.json.

```json
{
  "pipeline": {
    "debounce": {
      "daily_summary_seconds": 90,
      "daily_summary_batch_max_cards": 10,
      "group_summary_seconds": 15,
      "event_batch_window_seconds": 3,
      "event_batch_max_size": 10
    }
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pipeline.debounce.daily_summary_seconds` | `90` | Quiet period before folding accumulated cards into the daily summary. Each new card resets the timer; when it expires, all cards that arrived in the window are folded together (see `daily_summary_batch_max_cards`). **Higher** = more cards coalesced per flush (fewer LLM calls during bursts) but slightly delayed summary updates. Set to `0` for immediate processing. |
| `pipeline.debounce.daily_summary_batch_max_cards` | `10` | Maximum cards folded into a **single** daily-summary LLM call. A flush with more fresh cards than this is split into `ceil(N / K)` batched calls rather than one giant prompt, so a large burst can't overflow a small local-model context window. **Lower** = safer for small context windows; **higher** = fewer calls per burst. Minimum `1`. |
| `pipeline.debounce.group_summary_seconds` | `15` | Per-entity quiet period before updating the group summary. Multiple cards arriving for the same entity within this window are processed in one LLM call instead of N calls. **Higher** = more batching, fewer calls. Set to `0` to disable (immediate per-card updates). |
| `pipeline.debounce.event_batch_window_seconds` | `3` | Collection window for batch-routing. The queue processor waits this long for additional events to arrive before classifying. Multiple events classified in one LLM call instead of individually. Set to `0` to disable batching (events process immediately). |
| `pipeline.debounce.event_batch_max_size` | `10` | Maximum events to classify in a single batch-router call. Larger batches save more calls but increase per-call latency and output token usage. |

Changes take effect on the next pipeline run (no restart required).

---

## Distance Values Explained

Laya uses **cosine distance** in ChromaDB (range 0.0 to 2.0, but practically 0.0 to ~1.0):

| Distance | Meaning | Typical Use |
|----------|---------|-------------|
| 0.00–0.10 | Near-identical content | Same event, different wording |
| 0.10–0.20 | Very similar | Same topic, closely related |
| 0.20–0.35 | Moderately similar | Related topics, same domain |
| 0.35–0.50 | Loosely related | Same category but different subjects |
| 0.50+ | Unrelated | Different domains entirely |

The embedding model is `nomic-ai/nomic-embed-text-v1.5` (768 dimensions).

---

## Full Example settings.json

```json
{
  "smart_grouping": {
    "context_association": true,
    "smart_display": true,
    "confidence_threshold": 0.22,
    "auto_confirm_threshold": 0.12
  },
  "tuning": {
    "context_association_time_window_days": 7,
    "semantic_entity_threshold": 0.35,
    "entity_search_results": 5,
    "classification_learn_threshold": 15,
    "classification_learn_batch": 50,
    "classification_learn_interval_hours": 6,
    "context_learn_threshold": 10,
    "context_learn_batch": 40,
    "context_learn_interval_hours": 6,
    "context_rules_max_injection": 20,
    "context_corrections_max_injection": 10,
    "trace_search_results": 30,
    "trace_max_seeds": 20,
    "trace_semantic_max_distance": 0.65,
    "chat_semantic_max_distance": 0.60,
    "chat_context_items": 12,
    "router_related_context_results": 3,
    "feedback_time_window_days": 30,
    "corrections_retention_days": 30
  }
}
```
