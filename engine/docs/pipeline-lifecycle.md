# Laya Pipeline Lifecycle — Prompt Stages

## Event Processing Pipeline (Main Flow)

```mermaid
flowchart TD
    %% ─── Event Arrival ───
    EV[/"Event arrives via n8n webhook"/]
    EV --> INGEST

    %% ─── Phase 1-3: Pre-processing (No LLM) ───
    subgraph PRE["Pre-processing (No LLM)"]
        INGEST["Ingest<br/><i>Resolve actor_relationship,<br/>parse participant_roles</i>"]
        SPACE["Space Resolution<br/><i>Map connection → space_id</i>"]
        RULES["Rules Engine<br/><i>User-defined filters</i>"]
        INGEST --> SPACE --> RULES
    end

    RULES -->|filtered| STOP([filtered — stop])
    RULES -->|pass| ROUTER

    %% ─── Phase 4: Router ───
    subgraph ROUTER_PHASE["Phase 4: Router"]
        ROUTER["🧠 Router LLM<br/><b>prompts/router.py</b><br/>role=router | temp=0.0<br/>structured output ✓"]
        ROUTER_OUT["RouterOutput:<br/>category, persona, priority,<br/>entities, requires_research"]
        ROUTER --> ROUTER_OUT
    end

    %% ─── Phase 5: Workers (conditional) ───
    ROUTER_OUT -->|requires_research=true| WORKERS
    ROUTER_OUT -->|requires_research=false| STAGER

    subgraph WORKER_PHASE["Phase 5: Workers (conditional)"]
        WORKERS["🧠 Persona Worker LLM<br/><b>prompts/{persona}.py</b><br/>role=router | temp=varies"]
        WORKER_LIST["Engineer · Comms · Ops<br/>Sales · HR · Finance"]
        WORKERS --- WORKER_LIST
    end

    WORKERS --> STAGER

    %% ─── Phase 6: Stager ───
    subgraph STAGER_PHASE["Phase 6: Stager"]
        STAGER["🧠 Stager LLM<br/><b>prompts/stager.py</b><br/>role=stager | temp=0.2<br/>structured output ✓"]
        STAGER_OUT["ActionCardData:<br/>header, summary, intelligence,<br/>staged_output, suggested_actions,<br/>suggested_tags, context_match,<br/>status_verdict"]
        STAGER --> STAGER_OUT
    end

    STAGER_OUT --> EMIT

    %% ─── Phase 7: Emit ───
    subgraph EMIT_PHASE["Phase 7: Emit"]
        EMIT["Decide card status (ready or done)<br/>Persist Card to SQLite<br/>Close earlier cards the event settles<br/>Embed in ChromaDB<br/>Broadcast via WebSocket"]
    end

    %% ─── Post-Emit Async Steps ───
    EMIT --> POST_EMIT

    subgraph POST_EMIT["Post-Emit (async, non-blocking)"]
        direction TB
        CTX["Context Grouping<br/><i>ChromaDB similarity +<br/>optional LLM confirm</i>"]
        ENT["Entity Resolution<br/><i>Semantic Layer 2<br/>(no LLM usually)</i>"]
        GS["🧠 Group Summary<br/><b>prompts/group_summary.py</b><br/>role=group_summary | temp=0.1<br/>debounced 15s per entity"]
        DS["🧠 Daily Summary<br/><b>prompts/summarizer.py</b><br/>role=stager | temp=0.2<br/>debounced 90s per space<br/>batched fold ≤10 cards/call"]
        OMNI_Q["Omni Queue<br/><i>Incremental update<br/>(no LLM)</i>"]
        TAGS["Tag Persistence"]
        PRULES["Processing Rules"]
    end
```

## Card Status (decided by the Stager)

A card is created `ready` or `done`. The stager LLM decides which, in the same
call that writes the card. The code has no list of platforms or event types for
this.

**What the stager is asked.** The rules are `CARD_STATUS_RULES` in
`llm/prompts/stager.py`, appended to the stager system prompt for every
platform. The stager returns a `card_status` object:

| Field | Values | Meaning |
|---|---|---|
| `reason` | one sentence | The evidence for `event_effect`. Written first. |
| `event_effect` | `concludes`, `reopens`, `standalone_notice`, `none` | What this one event does to its work item (the pull request, ticket, thread, meeting or page). |
| `status` | `ready`, `done` | Status of the card being written. |
| `settled_refs` | list of `#` numbers | Earlier ready cards this event settles while the work item stays open. |

**What the stager sees.** `[WORK ITEM TIMELINE]` in the user message lists the
earlier cards of the same `entity_id` (event time at or before this event,
oldest first, at most 40) and ends with "Before this event the work item was:
open | concluded". `pipeline/stager.py` builds it and maps the `#` numbers the
stager returns back to `card_id`s.

**What the code does with the answer** (`pipeline/card_status.py`, called from
`emit.run_emit` under a per-entity lock):

1. Validate. An `event_effect` or `status` outside the allowed values drops the
   whole answer and the card is `ready`.
2. Work item state before the event: the most recent earlier card whose stored
   `status_effect` is `concludes` or `reopens` decides it. No such card means
   open.
3. Work item state after the event: `concludes` gives concluded, `reopens`
   gives open, `none` and `standalone_notice` leave it unchanged.
4. The new card is `done` only when the state after the event is concluded and
   the stager said `done`. A `done` on an open work item is ignored.
5. `concludes` closes every earlier `ready` card of the entity. Otherwise the
   cards named in `settled_refs` are closed.
6. A card for an older event that is emitted after a later event already
   concluded the work item is created `done`.

Limits: only `ready` cards are closed, only cards with event time at or before
the closing event, and never a card the user reopened after Laya closed it. A
status set by a persona worker is kept.

**Stored on `action_cards`** (migration 075): `status_effect` (the stager's
answer), `status_reason` (why Laya set the card to `done`), `status_set_by`
(`laya`, or `user` after the user reopened a card Laya closed). A card Laya
closes keeps `read_at` NULL, so it still counts as unread.

**Switch.** `pipeline.llm_card_status` in settings.json (Settings → Models →
Advanced), default on. When off, the rules and the `card_status` schema object
are not sent, every card is created `ready`, and Laya closes nothing.

**Override.** `~/.laya/prompts/stager_card_status.md` replaces the rules. A
`stager.md` override does not remove them.

## Omni Resynthesis (Scheduled)

```mermaid
flowchart LR
    TRIGGER[/"Scheduled or manual trigger"/]
    TRIGGER --> GATE["Gate queue<br/>processing"]
    GATE --> FETCH["Fetch snapshot +<br/>new/acted/resolved cards"]
    FETCH --> LLM["🧠 Omni Resynthesis LLM<br/><b>prompts/omni.py</b><br/>role=omni | temp=0.3<br/>structured output ✓"]
    LLM --> SECTIONS["Sections:<br/>attention · recent ·<br/>period · milestone"]
    SECTIONS --> PERSIST["Persist full<br/>base snapshot"]
    PERSIST --> UNGATE["Ungate queue"]
    UNGATE --> BROADCAST["Broadcast<br/>omni_updated"]
```

## Trace (User-Initiated Search)

```mermaid
flowchart TD
    USER[/"User initiates Trace search"/]

    subgraph DISCOVERY["Phase 1: Discovery (No LLM)"]
        ID_SEARCH["Identifier search<br/>(PR-540, BUG-123)"]
        SEM_SEARCH["Semantic search<br/>(ChromaDB)"]
        ENT_SEARCH["Entity table lookup"]
        TXT_SEARCH["SQL keyword/fuzzy"]
        RRF["Reciprocal Rank Fusion<br/>(merge all signals)"]
        ID_SEARCH & SEM_SEARCH & ENT_SEARCH & TXT_SEARCH --> RRF
    end

    USER --> DISCOVERY

    RRF -->|optional| FILTER
    RRF -->|skip filter| EXPAND

    FILTER["🧠 Relevance Filter LLM<br/><b>prompts/trace_filter.py</b><br/>role=router | temp=0.0<br/>structured output ✓"]
    FILTER --> EXPAND

    subgraph EXPAND_CLUSTER["Phase 2-3: Expand & Cluster (No LLM)"]
        EXPAND["Expand seeds →<br/>fetch all entity cards"]
        CLUSTER["Union-Find clustering<br/>+ chapter detection"]
        EXPAND --> CLUSTER
    end

    CLUSTER --> NARRATIVE

    subgraph NARRATIVE_PHASE["Phase 4: Narrative (Streaming LLM)"]
        NARRATIVE["🧠 Narrative Generation<br/><b>prompts/trace.py</b><br/>role=trace | temp=0.3<br/>streaming ✓"]
        SUMMARY["🧠 Trace Summary<br/><b>prompts/trace.py</b><br/>role=trace | temp=0.3<br/>streaming ✓"]
    end
```

## Chat (Multi-Turn with Tools)

```mermaid
flowchart TD
    MSG[/"User sends message"/]
    MSG --> CTX_FETCH["Hybrid context retrieval<br/>(ChromaDB + SQL FTS5:<br/>cards, events, entities,<br/>Corpus document chunks)"]
    CTX_FETCH --> HISTORY["Load chat history<br/>(last 10 messages)"]
    HISTORY --> BUILD["Build messages:<br/>system + history + user + context"]

    BUILD --> CHAT_LLM["🧠 Chat LLM (streaming)<br/><b>prompts/chat.py</b><br/>role=chat | temp=0.3<br/>tools ✓ (all read/write/egress)"]

    CHAT_LLM -->|tool_call| TOOL_EXEC["Execute tool<br/>(search_cards, get_event,<br/>search_documents, read_document,<br/>dismiss_card, egress, ...)"]
    TOOL_EXEC -->|result| CHAT_LLM
    CHAT_LLM -->|"no more tools<br/>(max 20 iterations)"| RESPONSE["Final response<br/>streamed to user"]

    MSG -.->|"background<br/>(first msg only)"| TITLE["🧠 Title Generation<br/><b>prompts/chat.py</b><br/>role=router | temp=0.3"]
```

## Corpus Document Ingest (User-Initiated)

```mermaid
flowchart TD
    DROP[/"File dropped, picked or uploaded"/]
    DROP --> CREATE["Create document row<br/><i>validate type + size, sha256,<br/>dedupe per space, record path + mtime</i>"]
    CREATE --> PARSE["Parse<br/><i>documents/parsers (no LLM)</i>"]
    PARSE --> CHUNK["Chunk<br/><i>page/heading aware, max 3000 chars</i>"]
    CHUNK --> EMBED["Embed<br/><i>batches of 8 into laya_memory<br/>content_type=document_chunk</i>"]
    EMBED --> LINK["Link<br/><i>identifiers + entity names -> document_entity_links</i>"]
    LINK --> BUDGET{budget paused?}
    BUDGET -->|yes| READY
    BUDGET -->|no| MAP["🧠 Summary map (per window)<br/><b>prompts/document_summary.py</b><br/>role=stager (or document_summary) | temp=0.2<br/>structured output ✓ | ≤2 in flight"]
    MAP --> REDUCE["🧠 Summary reduce<br/><b>prompts/document_summary.py</b><br/>role=stager (or document_summary) | temp=0.2<br/>structured output ✓ | hierarchical by 8"]
    REDUCE --> SUMVEC["Embed summary<br/>content_type=document_summary"]
    SUMVEC --> READY["status=ready<br/><i>every transition broadcasts document_updated</i>"]
    PARSE -->|no text / encrypted| FAIL([failed])
    EMBED -->|encoder error after single retries| FAIL
    MAP -.->|LLM error| READY
```

Parsing and chunking failures are fatal; a summary failure records `error_code=summary_error` on a document that is still `ready` and searchable. `POST /documents/{id}/reprocess?stage=embed|summarize|link` re-runs one stage from the stored chunks.

## Learning Pipelines (Feedback-Driven)

```mermaid
flowchart LR
    subgraph TRIGGERS["Triggers"]
        CORR[/"≥15 user corrections"/]
        LINK[/"≥10 link/unlink actions"/]
    end

    CORR --> LEARN["🧠 Classification Learner<br/><b>prompts/learner.py</b><br/>role=router | temp=0.2<br/>structured output ✓"]
    LEARN --> CLASS_RULES["New classification_rules<br/>(fed back to Router)"]

    LINK --> CTX_LEARN["🧠 Context Learner<br/><b>prompts/context_learner.py</b><br/>role=router | temp=0.2<br/>structured output ✓"]
    CTX_LEARN --> CTX_RULES["New context_rules<br/>(fed back to Context Grouping)"]
```

## Briefing (Scheduled/Manual)

```mermaid
flowchart LR
    SCHED[/"Scheduled or manual trigger"/]
    SCHED --> GATHER["Gather overnight events,<br/>pending cards, calendar"]
    GATHER --> BRIEF_LLM["🧠 Briefing LLM<br/><b>prompts/briefing.py</b><br/>role=stager | temp=0.3"]
    BRIEF_LLM --> SYNTH["Create synthetic event"]
    SYNTH --> PIPELINE["Feed through normal pipeline<br/>(Router → Stager → Emit)"]
    PIPELINE --> CARD["Briefing card in feed"]
```

## Complete LLM Touchpoint Summary

```mermaid
graph LR
    subgraph MAIN["Main Pipeline<br/>(per event)"]
        R["Router<br/>router · 0.0"]
        W["Workers<br/>router · varies"]
        S["Stager<br/>stager · 0.2"]
        R --> W --> S
    end

    subgraph POSTEMIT["Post-Emit<br/>(debounced)"]
        GS["Group Summary<br/>group_summary · 0.1"]
        DS["Daily Summary<br/>stager · 0.2"]
        OQ["Omni Incremental<br/>(no LLM)"]
    end

    subgraph SCHEDULED["Scheduled / Manual"]
        OR["Omni Resynthesis<br/>omni · 0.3"]
        BR["Briefing<br/>stager · 0.3"]
        LN["Learner<br/>router · 0.2"]
    end

    subgraph USER_DRIVEN["User-Driven"]
        CH["Chat<br/>chat · 0.3 · tools"]
        TR["Trace<br/>trace · 0.3 · streaming"]
        DOC["Corpus Summary<br/>stager · 0.2 · map-reduce"]
    end

    S -.-> GS & DS & OQ
    OR -.-> |"resynthesis"| OQ
```

## Date/Time Injection

All LLM calls receive temporal awareness via centralized injection in `llm_call()`:

```
[Current date/time: 2026-06-07 14:30:00 UTC (Saturday)]

{actual user message content}
```

This is injected into the **last user message** (not the system prompt) to preserve prompt caching.
