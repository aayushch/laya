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

## Trace / RAG Search

Parameters that control Laya's semantic search for the Trace feature (deep research on cards).

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

## Pipeline Debounce & Batching

Parameters that control how Laya batches and debounces LLM calls for efficiency. These reduce the number of LLM calls during burst activity without affecting card quality. Located under `pipeline.debounce` in settings.json.

```json
{
  "pipeline": {
    "debounce": {
      "daily_summary_seconds": 30,
      "group_summary_seconds": 15,
      "event_batch_window_seconds": 3,
      "event_batch_max_size": 10
    }
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pipeline.debounce.daily_summary_seconds` | `30` | Quiet period before processing accumulated cards into the daily summary. Each new card resets the timer. **Higher** = fewer LLM calls during bursts but slightly delayed summary updates. Set to `0` for immediate processing. |
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
