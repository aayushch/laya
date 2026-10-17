# Corpus: user-provided documents in Laya's retrieval stack

Implementation runbook. Status: **implemented** (2026-10-17, branch `feat/corpus-documents`).
Section 6 lists the order the work landed in; sections 4 and 5 describe the
code as built.

Naming: the user-facing feature is **Corpus**, a peer of Pulse, Omni and
Coherence in the navigation. The name applies to the UI label, the route
(`/corpus`) and the component folder only. Engine code, tables, API paths,
tool names and WebSocket messages say `documents`, the same way the Pulse
route is `feed` in code.

This document is self-contained. An implementer who has never seen the
discussion that produced it should be able to build the feature end to end
from this file plus the repository. Every seam in existing code is named
with its file and function; line numbers are as of commit `f4b0003` and are
hints, not contracts.

---

## 1. Goal and scope

Laya already has an end-to-end hybrid retrieval stack: ChromaDB vectors plus
SQLite FTS5 BM25, fused by Reciprocal Rank Fusion, feeding the chat tool loop,
Coherence traces and the MCP server. Today the only content that enters that
stack is action cards built from ingested platform events.

Corpus lets the user hand Laya a local file (PDF, DOCX, Markdown, plain
text, CSV, JSON, HTML) and have it parsed, chunked, embedded and indexed, so
that:

1. the in-app chat can answer questions about it with page-level citations,
   either ambiently (any chat in the space) or with the document explicitly
   attached to a conversation;
2. documents are cross-referenced with the event stream: cards show related
   documents, and Coherence traces get a documents cluster;
3. external agents can search and read documents through Laya's MCP server;
4. a file can be dropped anywhere on the app window, or picked from the Corpus
   page, and every document the user has shared is visible there.

The file itself is **not copied**. For a drop or a picker selection the
engine reads the file at its path, stores the extracted text and vectors, and
remembers the path, a content hash and the modification time. Everything
Phase 1 does (chat, search, citations, related cards, MCP) is served from
SQLite and Chroma, so a moved or deleted original leaves the document fully
usable. Only the browser multipart upload, which has no path to reference,
keeps a stored copy.

### Decisions already taken

| Question | Decision |
|---|---|
| Persistence and scope | **Per-space Corpus plus attach.** A document belongs to one space and is retrievable by every chat and trace in that space. A conversation can additionally attach document ids to focus retrieval on them. |
| File storage | **Reference, not copy.** Drops and picker selections record `source_path` + sha256 + mtime and read in place. A stored copy exists only for multipart uploads (browser or remote engine). Reprocess re-reads the path; a missing or changed source is reported, never fatal. |
| Name | **Corpus** in the UI and route; `documents` everywhere in code. |
| Is a document a card? | **No.** Documents are a first-class content type: own tables, own FTS table, own Chroma `content_type` values. No feed card is created on ingest. A later opt-in "Extract actions" feature will run a document through the synthetic-event path that `pipeline/briefing.py` uses (platform `laya`); the data model must allow that, Phase 1 does not build it. |
| Parser dependencies | **Core, pure-Python, bounded pins.** `pypdf` and `mammoth`. No PyMuPDF (AGPL), no docling or unstructured (heavy model stacks), no `lxml` in Phase 1 (native wheel not in the lock set). |
| Phase 1 contents | Chat Q&A with citations; LLM map-reduce summary at ingest; related documents on cards and a Coherence cluster; MCP read tools; drag-drop anywhere plus the Corpus page with select-and-ask. |

### Explicitly deferred to Phase 2

PPTX and XLSX parsers; an original-file viewer with page deep links; folder
watching and re-sync when a file changes on disk; "Extract actions" into
cards; grounding persona drafts in linked documents; "re-embed all" after an
embedding model switch; a document-only discovery signal in trace; OCR.

---

## 2. Facts about the current code this design rests on

All verified against the repository. Where a plausible assumption turned out
to be wrong, the correction is stated.

### ChromaDB (`engine/laya/db/chromadb_store.py`)

- One collection, `COLLECTION_NAME = "laya_memory"`, under `LAYA_DATA_DIR/chromadb`;
  cosine space, `construction_ef=200`, `M=32`, `search_ef=150`.
- Embedding backends in `EMBEDDING_MODELS`: nomic v1.5 (768d, `search_document:` /
  `search_query:` prefixes), mpnet (768d, default for fresh stores), minilm (384d).
  `_get_configured_model_key` keeps nomic only on stores that already hold
  vectors. Without sentence-transformers it falls back to Chroma's built-in
  MiniLM. `get_embedding_info()["backend"]` reports the active backend key.
- Encoding is a synchronous SentenceTransformer call under a module-level
  `threading.Lock` (`_encode_lock`), run from async code via an executor.
- `embed_document(doc_id, text, metadata)` upserts one vector. `embed_document_chunked`
  exists with **no callers**: it re-chunks with `db/chunking.chunk_text` (losing
  page boundaries) and upserts one chunk at a time. Leave it untouched.
- `delete_document(doc_id)` deletes only that exact id. `update_document_metadata(doc_id, patch)`
  merges metadata into the base id and every vector whose `parent_id` equals it,
  without re-embedding; the card-move flow in `api/cards_lifecycle.py` uses it.
- `memory_search(query, n_results, where, max_distance)` returns
  `{id, document, metadata, distance}`.
- The only writer today is `pipeline/emit.py` (around line 413), with metadata
  `{"content_type": "card_summary", "card_id", "source_event_id", "source_platform",
  "entity_id", "entity_refs", "persona", "priority", "timestamp": <float epoch>,
  "space_id", "tags": <csv>}`. `content_type` is the only discriminator and
  `card_summary` its only value.
- **There is no re-embed routine for cards.** A model switch requires deleting
  the collection directory. Documents therefore need their own re-embed path,
  which is cheap because chunk text is kept in SQLite.
- **Six readers query the collection with no `content_type` filter and assume
  every hit is a card** (they read `card_id` or `entity_refs` from metadata):
  - `pipeline/chat.py:_semantic_search` (~L850)
  - `pipeline/trace.py:_semantic_search` (~L598)
  - `pipeline/related_context.py` (~L55, stager context)
  - `pipeline/entity_resolution.py` (~L48, reads `entity_refs`)
  - `pipeline/context_grouping.py` (~L96-103, hydrates `card_id` from `action_cards`)
  - `llm/tools/card_tools.py` (~L46-64, `search_cards` semantic path)
  - `llm/tools/search_tools.py` (~L43) is the generic `semantic_search` tool and
    is the one place where document hits are desirable.

### FTS5 (`engine/laya/db/fts.py`)

- Two standalone FTS5 tables (own copy of the text), tokenizer
  `porter unicode61`: `cards_fts(card_id UNINDEXED, header, summary, intelligence, thread_context)`
  and `events_fts(event_id UNINDEXED, subject_title, content_body)`.
- Triggers keep them in sync. `ensure_fts_tables(db)` runs at startup after
  migrations, creates tables and triggers in code, and backfills only when an
  index is empty. `fts_ready()` reports availability. `build_fts_match(query, *, min_len=3, max_terms=8, match_all=False)`
  builds the MATCH expression. LIKE fallbacks live in each caller.
- **Migrations cannot contain triggers.** `db/migrate.py` wraps every migration
  file in `BEGIN;`/`COMMIT;` and its comment states that migrations contain no
  BEGIN/COMMIT or triggers. All FTS DDL for documents goes in `db/fts.py`.

### Shared retrieval primitives (`engine/laya/retrieval.py`)

`extract_keywords(query, *, min_len=3, max_terms=None)`,
`reciprocal_rank_fusion(ranked_lists, k=60)` (dedupe key: `id`, else `card_id`,
`event_id`, `entity_id`, else rank), and
`async fts_or_like(query, *, min_len, max_terms, match_all=False, fts, like, warn_event)`.

### Chat (`engine/laya/pipeline/chat.py`)

- Entry points: `process_chat_message` (REST, `api/chat_api.py`) and
  `process_chat_message_streaming` (WebSocket `chat_message`, `api/ws_router.py:_handle_chat_message`).
- Retrieval is skipped when `card_context` is set or the previous assistant
  turn used tools (`_prev_turn_used_tools`).
- `_retrieve_context(user_message, space_id, token_budget=2000)` gathers four
  searches in parallel (`_semantic_search` n=10 `where={"space_id"}` `max_distance=0.60`,
  `_card_keyword_search` n=10, `_event_keyword_search` n=10, `_entity_search` n=5),
  fuses with RRF k=60, dedupes on `id`, takes the top 12, packs with
  `_pack_context` (chars/4 token estimate).
- Item shape: `{id, card_id?, event_id?, type, text, metadata, source}`;
  `_format_context_item` renders card, event and entity branches plus a generic
  fallback `[{platform}] {text[:500]}`.
- Citation regexes near the top of the file extract `[card:ID]` and `[event:ID]`
  into `chat_messages.referenced_cards` / `referenced_events` (JSON arrays,
  migration `005_chat.sql`). `canonical_card_ids(card_ids)` (~L56) produces the
  canonical sorted-JSON form stored in `chat_conversations.card_ids`
  (migration `051_chat_card_context.sql`). `_ensure_conversation` (~L71) creates
  the conversation row.
- `ChatRequest` (`models/chat.py`) is `{message, space_id, conversation_id, card_context, card_ids}`.
  The UI's WebSocket payload sends no `space_id`.
- Prompt assembly is `llm/prompts/chat.py:build_chat_messages`; retrieved
  context is appended to the user message between `[RETRIEVED CONTEXT]` markers,
  `card_context` becomes a "## Active Card Context" system block.
- Tool loop: `MAX_TOOL_ITERATIONS = 20`; results capped at 12,000 chars
  (`_cap_tool_result`); `execute_tool(name, args, space_id=)`.
- Tool gating: `llm/tools/definitions.py` `get_all_tool_definitions()` (~L11),
  `select_tool_definitions(text)` (~L62), `select_chat_tools(user_message, chat_history)` (~L75),
  `read_tool_names()` (~L99). Read and card-write groups always ship; settings,
  rules and egress groups ship on keyword hits. `tests/test_tool_gating.py::test_plain_turn_ships_only_read_write`
  asserts that a plain turn ships exactly read + write.
- `llm/client.py`: `_get_space_model` (~L224) tolerates roles with no
  `{role}_model` column on `spaces`; `_ROLE_FALLBACKS` (~L270) maps roles to
  defaults. `llm_call` writes `audit_log` rows that `pipeline/budget.py` sums
  by `STEP_TO_FEATURE` (~L29).

### Trace / Coherence (`engine/laya/pipeline/trace.py`)

`_run_trace_inner` discovers seeds (`_identifier_search` with `_IDENTIFIER_RE` ~L120,
`_semantic_search` max_distance 0.65 returning `card_id`/`entity_id`,
`_entity_table_search`, `_card_text_search`, optional fuzzy and event keyword
search), fuses with RRF, applies feedback, `_expand_seeds` (~L868) pulls every
card for each entity id, optional LLM relevance filter, `_build_clusters` (~L1054,
runs in an executor with precomputed inputs), `_save_trace` (~L1250).
`api/trace_api.py:_reconstruct_trace` (~L375) hydrates saved traces.
`_IDENTIFIER_RE` is loose (`[A-Za-z]{1,10}[\s\-#]?\d{1,6}`) and matches
"Figure 3", "Q4 2025" and "page 12"; it is fine for short user queries but
would flood links if run over document text.

### Models and tables

- `events`: `timestamp` is platform time, `created_at` is ingest time.
  `action_cards.event_id` is NOT NULL. Neither fits a document.
- Non-event content today (`daily_summaries`, `omni_snapshots`, `group_summaries`,
  `traces`, `metadata` key/value) is neither embedded nor in FTS.
- The pattern for non-platform content entering the pipeline is a synthetic
  event: `pipeline/briefing.py` (~L203) builds a `LayaEvent` with platform
  `laya` and subject type `briefing`; `api/cards_agent.py` inserts
  `laya:agent_run` events. "Extract actions" will reuse this in Phase 2.
- Migrations run `001` to `073`; the next number is **074**.
- Multi-statement invariants use `db/sqlite.transaction()`; timestamps use
  `db/timeutil.py` (`db_now()`).

### Existing upload and drag-drop machinery

- `api/cards_agent.py`: `POST /upload-agent-file` (multipart `UploadFile`,
  writes `~/.laya/tmp/agent-staging/<uuid>.<ext>`, extension sanitised, MIME
  map), `POST /upload-agent-file-path` (`{path}`; exists because Tauri on macOS
  does not deliver OS file drops to the DOM, so the UI receives absolute paths
  from Tauri's native drag-drop event), `POST /delete-agent-staging-file`
  (path confinement by `os.path.normpath` + `Path.relative_to`, deliberately
  not `resolve()` to keep CodeQL's path-injection check satisfied).
- `ui/src/lib/components/agent/RunAgentModal.svelte` ~L255-299: window-level
  HTML5 `dragover`/`drop`/`dragleave` handlers plus a Tauri
  `getCurrentWebviewWindow().onDragDropEvent` subscription, both active only
  while the modal is open. `ui/src/lib/api/engine.ts` ~L328-345 has
  `uploadAgentFile`, `uploadAgentFilePath`, `deleteAgentStagingFile`.
- Tauri: `tauri-plugin-dialog` 2.8.0 is registered with `dialog:default`; only
  folder pickers use it. No `tauri-plugin-fs`. `dragDropEnabled` is default.

### UI facts

- Chat: `ui/src/lib/components/chat/{ChatSidebar,ChatMessage,ChatConversationList}.svelte`,
  stores `lib/stores/chat.ts` (`chatCardContext`, `chatCardIds`, `chatOpen`,
  `activeConversationId`) and `lib/stores/chatStream.ts` (module-level WS
  stream handler, initialised from the root layout). `ChatSidebar.send()`
  (~L192) sends `{type:'chat_message', payload:{message, conversation_id, card_context?, card_ids?}}`
  over WS and falls back to `engineApi.sendChat`. `ChatMessage.svelte:replaceMarkers`
  (~L102) linkifies `[card:ID]`, bare `card_<12hex>` and `[event:ID]`.
- **There is no single "active space" in the UI.** The feed uses a
  multi-select `feedFilters.spaceFilter`; chat sends no space id. Any
  "add to space" surface needs its own picker.
- Navigation in `ui/src/routes/+layout.svelte` (~L550-600) links Feed, Omni,
  Coherence; routes exist for coherence, dashboard, feed, legal, omni,
  settings, setup, status, workspace. `CardDetail.svelte` has sections
  (Header, Tags, Intelligence, Staged output, Suggested actions, Metadata) and
  a footer with "Move to space".
- WebSocket: `lastMessage` store in `lib/stores/websocket.ts`; engine side
  broadcasts via `laya.api.websocket.manager.broadcast` (used for
  `card_updated`, `trace_complete`).
- UI conventions from the project: Svelte 5 runes only; no emoji icons;
  toolbar buttons are disabled, never hidden; every engine call goes through
  `request`/`engineFetch` in `lib/api/engine.ts`.

### Dependencies

`engine/requirements.txt` has no document parser of any kind. Relevant pins:
`chromadb>=1.5.4,<2`, `tiktoken>=0.12.0`, `mcp>=1.0.0,<2`; `cryptography 46.0.5`
is already in `requirements.lock` (pypdf uses it for AES-encrypted PDFs);
`lxml` is **not** in the lock. Hand-edited ranges are locked by
`scripts/lock-deps.sh`, checked by `scripts/check-locks.sh` (wheel
availability per OS and Python 3.10-3.14) and `scripts/smoke-install.sh`.
An unbounded `mcp>=1.0.0` once broke fresh installs when 2.x shipped: every
new pin must have an upper bound.

---

## 3. Architecture overview

```
drop / picker                              browser or remote-engine upload
        │                                              │
        ▼                                              ▼
POST /documents/from-path ({path})         POST /documents (multipart)
        │  validate ext + size, sha256,                │  same, but the bytes are written to
        │  dedupe per space, record path +             │  ~/.laya/data/documents/<id>.<ext>
        │  mtime (storage_mode=reference)              │  (storage_mode=copy)
        └──────────────────────┬───────────────────────┘
                               ▼
documents row (status=queued)  ──►  run_ingest task
        │
        ├─ parsing      parsers/<ext>.py → ParsedDocument(units[TextUnit(text,page,heading)])
        ├─ chunking     documents/chunking.chunk_units → document_chunks rows (FTS triggers fill documents_fts)
        ├─ embedding    chromadb_store.embed_batch → laya_memory (content_type=document_chunk)
        ├─ linking      documents/linking → document_entity_links (identifiers, entity refs, mentions)
        ├─ summarizing  documents/summary (map-reduce) → documents.summary + one document_summary vector
        └─ ready        (each transition broadcasts document_updated over /ws)

Consumers
  chat   _retrieve_context ← _document_vector_search + _document_keyword_search (RRF with cards/events/entities)
         tools: list_documents / search_documents / read_document   (also exposed via MCP read scope)
         citations [doc:ID p.N] → chat_messages.referenced_documents
  cards  GET /cards/{id}/related-documents  (via action_cards.entity_id ↔ document_entity_links)
  trace  TraceCluster.documents from document_entity_links of the cluster's entity ids
```

Ids: `document_id = "doc_" + uuid4().hex[:12]`; `chunk_id = f"{document_id}_chunk_{index}"`.
The chunk id is both the SQLite primary key and the Chroma vector id, so a
vector hit and a BM25 hit on the same chunk fuse in RRF.

### User flow

1. **Share.** The user drops a file anywhere on the window, drops it on the
   Corpus page, or uses "Add" there. A sheet shows one tile per file with a
   space picker. Tiles move through parsing, embedding, linking and
   summarising with WebSocket-fed progress, then show ready with page count
   and summary. The sheet is the only transient surface.
2. **See.** Every shared document lives on the Corpus page, grouped by
   space, with status, pages, summary, source state (in place, moved,
   changed on disk), and actions: open, rename, delete, reprocess, re-sync,
   ask.
3. **Ask generally.** Open chat and ask. Documents in the space are retrieved
   ambiently alongside cards and events; no attachment needed.
4. **Ask specifically.** Select one or more documents on the Corpus page and
   press "Ask", or use the paperclip in the chat composer, or "Ask about
   this" on an open document. A new conversation opens with those documents
   attached as chips. The engine focuses retrieval on them, raises the
   retrieval budget, pins each summary into the system prompt, and ships the
   read and search tools. The attachment is saved on the conversation, so
   reopening it keeps the focus.
5. **Follow citations.** Replies cite `[doc:ID p.N]`; the link opens the
   document on the Corpus page at that page. Cards whose entity the document
   mentions show it under "Related documents", and Coherence traces show it
   as a cluster chip.

---

## 4. Engine

### 4.1 Package layout `engine/laya/documents/`

```
engine/laya/documents/
  __init__.py        # re-exports: create_from_bytes, create_from_path, run_ingest,
                     #   delete_document_cascade, recover_stalled_documents
  store.py           # SQL helpers
  ingest.py          # lifecycle state machine, task registry, recovery, delete cascade
  chunking.py        # page/heading-aware chunking over db/chunking.chunk_segments
  linking.py         # identifier + entity-mention extraction → document_entity_links
  summary.py         # map-reduce summary driver
  search.py          # hybrid chunk search shared by chat retrieval and the search_documents tool
  parsers/
    __init__.py      # registry keyed by extension
    base.py          # TextUnit, ParsedDocument, errors, DocumentParser ABC
    pdf.py           # pypdf, one TextUnit per page, scanned-PDF detection
    docx.py          # mammoth → markdown → markdown.py heading splitter
    markdown.py      # .md: heading-aware sections
    text.py          # .txt: paragraph units
    csv_.py          # stdlib csv
    json_.py         # stdlib json
    html.py          # stdlib html.parser
```

The parser registry mirrors `egress/platforms/`: `base.py` is the analogue of
`egress/platforms/base.py` (abstract attributes checked at instantiation,
one `PARSER = XParser()` module singleton per file), `parsers/__init__.py`
the analogue of `egress/platforms/__init__.py` (`_REGISTRY`, `for_extension(ext)`,
`supported_extensions()`, `registry_parsers()`). Adding PPTX or XLSX later is
one new file plus one registry entry.

#### `parsers/base.py`

```python
@dataclass
class TextUnit:
    text: str
    page: int | None = None      # 1-based page (PDF); None for formats without pages
    heading: str | None = None   # nearest enclosing heading (md, docx, html)

@dataclass
class ParsedDocument:
    units: list[TextUnit]
    page_count: int | None = None
    title: str | None = None     # from file metadata or the first H1, if any
    metadata: dict[str, Any] = field(default_factory=dict)

class ParseError(Exception):
    error_code = "parse_error"

class NoExtractableText(ParseError):   # scanned PDF, image-only document
    error_code = "no_text"

class EncryptedDocument(ParseError):
    error_code = "encrypted"

class UnsupportedFormat(ParseError):
    error_code = "unsupported"

class DocumentParser(abc.ABC):
    name: str
    extensions: frozenset[str]            # lower-case, no dot
    mime_types: frozenset[str] = frozenset()

    @abc.abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...   # synchronous; ingest runs it in a thread
```

Parser behaviour:

- **pdf.py**: `pypdf.PdfReader(path)`. If `reader.is_encrypted` and
  `reader.decrypt("") == 0`, raise `EncryptedDocument`. One `TextUnit(page=i+1)`
  per page from `page.extract_text()`. Scanned-PDF rule: if the total stripped
  text length is below `max(40, 25 * page_count)`, raise
  `NoExtractableText("No extractable text; this looks like a scanned PDF (OCR is not supported)")`.
  Title from `reader.metadata.title` when non-empty.
- **docx.py**: `mammoth.convert_to_markdown(fileobj).value`, then reuse the
  Markdown splitter from `markdown.py`. Mammoth warnings are logged, not fatal.
- **markdown.py**: split on ATX headings (`^#{1,6}\s`); each section becomes
  one or more units carrying the heading text; title is the first H1.
- **text.py**: split on blank lines into paragraph units; no headings.
- **csv_.py**: stdlib `csv`; header row kept; rows rendered as
  `col: val | col: val` lines, grouped roughly 50 rows per unit; heading = file stem.
- **json_.py**: pretty-print with indent 2; split by top-level keys (object)
  or slices of 50 items (array); heading = the key path.
- **html.py**: stdlib `html.parser`; drop `script`/`style`/`nav`; `h1-h6`
  text becomes the current heading; block elements separate units; title from
  `<title>`.

Every parser normalises whitespace (collapse runs of spaces, keep paragraph
breaks) and drops empty units.

#### `db/chunking.py` extension and `documents/chunking.py`

Add to `db/chunking.py`:

```python
def chunk_segments(
    segments: list[tuple[str, T]],
    target_chars: int = _CHUNK_TARGET_CHARS,    # 1400
    overlap_chars: int = _CHUNK_OVERLAP_CHARS,  # 200
) -> list[tuple[str, list[T]]]:
    """Sentence-boundary packing with overlap, identical to chunk_text, where each
    sentence carries the tag of the segment it came from. Each returned chunk lists
    the distinct tags, in order, of the sentences it contains."""
```

and reimplement `chunk_text(text, ...)` as `[c for c, _ in chunk_segments([(text, None)], ...)]`
so existing callers (`embed_document_chunked`) see identical output. Add a
golden test to prove it.

`documents/chunking.py`:

```python
@dataclass
class DocChunk:
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    char_count: int

def chunk_units(units: list[TextUnit], *, target_chars=1400, overlap_chars=200, min_section_chars=300) -> list[DocChunk]
```

Rules: group consecutive units by heading; a section shorter than
`min_section_chars` is merged forward into the next section; within a section
call `chunk_segments([(u.text, (u.page, u.heading)) for u in section])`;
`page_start`/`page_end` are the min/max page among contributing tags;
`heading` is the section heading. Chunks never cross a section boundary
except through the tiny-section merge. Chunk text is prefixed with the
heading once (`"{heading}\n{text}"`) when a heading exists, so the embedded
text carries its context the way card text carries `thread_context`.

### 4.2 Migration `engine/laya/db/migrations/074_documents.sql`

```sql
-- Corpus documents: a first-class content type (not cards, not events). The
-- extracted text lives in document_chunks (source of truth for FTS and for
-- re-embedding) and the vectors in the shared laya_memory Chroma collection.
-- The original file is read in place (storage_mode='reference', source_path);
-- only multipart uploads keep an engine-owned copy (storage_mode='copy',
-- stored_path). Reading, searching and citing never need the original.
CREATE TABLE IF NOT EXISTS documents (
    document_id        TEXT PRIMARY KEY,
    space_id           TEXT NOT NULL DEFAULT 'default',
    title              TEXT NOT NULL,
    original_filename  TEXT NOT NULL,
    file_type          TEXT NOT NULL,          -- pdf|docx|txt|md|csv|json|html
    mime_type          TEXT,
    storage_mode       TEXT NOT NULL DEFAULT 'reference',  -- reference | copy
    source_path        TEXT,                   -- absolute path the user shared; the file read for reference mode
    source_mtime       REAL,                   -- st_mtime of source_path at ingest; change detection
    stored_path        TEXT,                   -- engine-owned copy; only for storage_mode='copy'
    sha256             TEXT NOT NULL,
    size_bytes         INTEGER NOT NULL,
    page_count         INTEGER,
    chunk_count        INTEGER NOT NULL DEFAULT 0,
    char_count         INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'queued',
    error              TEXT,
    error_code         TEXT,
    summary            TEXT,
    summary_model      TEXT,
    embedding_model    TEXT,                   -- chromadb_store backend key at embed time
    language           TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    indexed_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_space_created ON documents(space_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_space_sha ON documents(space_id, sha256);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id     TEXT PRIMARY KEY,             -- '<document_id>_chunk_<index>', also the Chroma vector id
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    page_start   INTEGER,
    page_end     INTEGER,
    heading      TEXT,
    char_count   INTEGER NOT NULL,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_document_chunks_doc ON document_chunks(document_id, chunk_index);

-- Links from a document to work items it mentions. entity_id is the same key as
-- action_cards.entity_id / entities.entity_id.
CREATE TABLE IF NOT EXISTS document_entity_links (
    document_id    TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    entity_id      TEXT NOT NULL,
    link_method    TEXT NOT NULL,              -- identifier | entity_ref | mention
    confidence     REAL NOT NULL DEFAULT 1.0,
    mention_count  INTEGER NOT NULL DEFAULT 1,
    first_page     INTEGER,
    matched_text   TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (document_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_doc_entity_links_entity ON document_entity_links(entity_id);

-- Conversation-level attachments, same canonical sorted-JSON form as card_ids (051).
ALTER TABLE chat_conversations ADD COLUMN document_ids TEXT;
-- [doc:ID] citations extracted from assistant replies, beside referenced_cards/_events (005).
ALTER TABLE chat_messages ADD COLUMN referenced_documents TEXT;
-- Identifier-only document matches of a trace (documents attached to clusters are
-- stored inside cluster_data).
ALTER TABLE traces ADD COLUMN documents TEXT;
```

Constants in `documents/store.py`:

```python
DOCUMENT_STATUSES = ("queued", "parsing", "chunking", "embedding", "linking", "summarizing", "ready", "failed")
TERMINAL_STATUSES = {"ready", "failed"}
ERROR_CODES = ("no_text", "encrypted", "unsupported", "too_large", "parse_error",
               "embed_error", "summary_error", "summary_skipped_budget", "interrupted")
```

`config.py`: `DOCUMENTS_DIR = LAYA_DATA_DIR / "documents"` beside `DB_PATH`,
created in `ensure_directories()`; it holds only multipart copies.
`DEFAULT_SETTINGS["documents"] = {"max_file_mb": 50, "summary_window_chars": 12000, "summary_max_windows": 24, "embed_batch_size": 32}`.

Source state is not a column. It is computed on read by
`store.source_state(row)`: `"copy"` for stored copies, else `stat(source_path)`
gives `"ok"` (exists, mtime equals `source_mtime`), `"changed"` (exists, mtime
differs; the hash is only recomputed on re-sync, not on list) or
`"missing"`. A `stat` per row is cheap on a local filesystem and avoids a
background watcher in Phase 1.

### 4.3 FTS (`engine/laya/db/fts.py`)

Append to the DDL executed by `ensure_fts_tables`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    chunk_id UNINDEXED, document_id UNINDEXED, title, heading, text,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ai AFTER INSERT ON document_chunks BEGIN
    INSERT INTO documents_fts(chunk_id, document_id, title, heading, text)
    VALUES (new.chunk_id, new.document_id,
            COALESCE((SELECT title FROM documents WHERE document_id = new.document_id), ''),
            COALESCE(new.heading, ''), new.text);
END;

CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ad AFTER DELETE ON document_chunks BEGIN
    DELETE FROM documents_fts WHERE chunk_id = old.chunk_id;
END;

CREATE TRIGGER IF NOT EXISTS document_chunks_fts_au AFTER UPDATE OF text, heading ON document_chunks BEGIN
    DELETE FROM documents_fts WHERE chunk_id = old.chunk_id;
    INSERT INTO documents_fts(chunk_id, document_id, title, heading, text)
    VALUES (new.chunk_id, new.document_id,
            COALESCE((SELECT title FROM documents WHERE document_id = new.document_id), ''),
            COALESCE(new.heading, ''), new.text);
END;

-- A title rename re-syncs every chunk row of that document.
CREATE TRIGGER IF NOT EXISTS documents_fts_title_au AFTER UPDATE OF title ON documents BEGIN
    UPDATE documents_fts SET title = new.title WHERE document_id = new.document_id;
END;
```

Backfill in `ensure_fts_tables`, after the events backfill, only when
`documents_fts` is empty:

```sql
INSERT INTO documents_fts(chunk_id, document_id, title, heading, text)
SELECT c.chunk_id, c.document_id, COALESCE(d.title, ''), COALESCE(c.heading, ''), c.text
FROM document_chunks c JOIN documents d ON d.document_id = c.document_id
```

The delete cascade (4.7) deletes `document_chunks` rows explicitly rather
than relying on `ON DELETE CASCADE`, so the `_ad` trigger fires per row.

Queries live in `documents/search.py:keyword_chunk_search(query, space_id, n, document_ids=None)`
via `retrieval.fts_or_like(min_len=3, max_terms=8, warn_event="document_fts_failed", ...)`:

```sql
-- FTS branch
SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.page_start, c.page_end, c.heading,
       d.title, d.file_type, d.space_id
FROM documents_fts
JOIN document_chunks c ON c.chunk_id = documents_fts.chunk_id
JOIN documents d ON d.document_id = c.document_id
WHERE documents_fts MATCH ? AND d.status = 'ready'
  [AND d.space_id = ?] [AND d.document_id IN (...)]
ORDER BY bm25(documents_fts, 0, 0, 3.0, 2.0, 1.0)
LIMIT ?
-- LIKE branch: same SELECT/JOIN, WHERE d.status='ready' [space] [ids]
--   AND (c.text LIKE ? OR c.heading LIKE ? OR d.title LIKE ?) per keyword (max 8), OR-joined
-- ORDER BY d.created_at DESC, c.chunk_index ASC LIMIT ?
```

### 4.4 Chroma (`engine/laya/db/chromadb_store.py`)

Chunk vector, id = `chunk_id`:

```python
{"content_type": "document_chunk", "document_id": doc_id, "parent_id": doc_id,
 "chunk_id": chunk_id, "chunk_index": i, "total_chunks": n,
 "page_start": p0 or 0, "page_end": p1 or 0, "heading": heading or "",
 "title": title, "file_type": file_type, "source_platform": "document",
 "space_id": space_id, "timestamp": created_epoch_float, "entity_refs": ids_csv}
```

Doc-level summary vector, id = `document_id`:

```python
{"content_type": "document_summary", "document_id": doc_id, "title": title,
 "file_type": file_type, "source_platform": "document", "space_id": space_id,
 "timestamp": created_epoch_float, "page_count": page_count or 0, "entity_refs": ids_csv}
```

`parent_id = document_id` on chunks means `update_document_metadata(document_id, patch)`
patches the summary vector and every chunk in one call (used by rename and
space move). Neither metadata dict ever carries a `card_id` key; that absence
is what card consumers key on.

New helpers:

```python
DOCUMENT_CONTENT_TYPES = ("document_chunk", "document_summary")
# $nin rather than content_type == "card_summary": legacy card vectors may predate the key.
CARD_CONTENT_FILTER = {"content_type": {"$nin": list(DOCUMENT_CONTENT_TYPES)}}

def and_where(*parts: dict | None) -> dict | None:
    """Combine Chroma where-clauses. Chroma requires an explicit {"$and": [...]} when
    more than one top-level key is present; None parts are skipped."""

async def embed_batch(ids: list[str], texts: list[str], metadatas: list[dict],
                      *, batch_size: int = 32, on_batch=None) -> int:
    """Upsert in slices. One encode per slice (collection.upsert accepts lists), each
    slice run in the executor, and `await asyncio.sleep(0)` between slices so a card
    emit waiting on _encode_lock is served between slices instead of after the
    whole document. on_batch(done, total) is awaited after every slice."""

async def delete_where(where: dict) -> None:
    """collection.delete(where=...) in the executor. delete_where({"document_id": id})
    removes the summary vector and every chunk in one call."""
```

Apply `CARD_CONTENT_FILTER` through `and_where` to the six card-only readers
listed in section 2. This change is independently shippable and must land
before any document vector is written. `search_tools.semantic_search` stays
unfiltered; its result dict gains `document_id`, `title`, `page_start`,
`page_end` when present in metadata.

Re-embedding: `documents.embedding_model` stores `get_embedding_info()["backend"]`
at embed time. `GET /documents` computes `needs_reembed = embedding_model != current backend`.
`POST /documents/{id}/reprocess?stage=embed` runs the embed stage only: read
chunks from SQLite, `delete_where`, `embed_batch`, re-upsert the summary
vector, update `embedding_model`. No parse, no LLM call.

### 4.5 Store (`documents/store.py`)

```python
async def insert_document(row: dict) -> None
async def get_document(document_id) -> dict | None
async def list_documents(space_id=None, status=None, q=None, limit=50, offset=0) -> tuple[list[dict], int]
def source_state(row) -> str                                                # copy | ok | changed | missing
def readable_path(row) -> Path                                              # stored_path for copies, else source_path; raises SourceMissing
async def set_status(document_id, status, *, error=None, error_code=None, **fields) -> None   # UPDATE + updated_at + commit
async def replace_chunks(document_id, chunks: list[DocChunk]) -> None      # inside transaction(); DELETE then INSERT
async def get_chunks(document_id, *, page_from=None, page_to=None, chunk_from=None, chunk_to=None, limit=None) -> list[dict]
async def replace_links(document_id, links: list[dict]) -> None             # inside transaction()
async def links_for_document(document_id) -> list[dict]
async def documents_for_entities(entity_ids: set[str], space_id=None) -> dict[str, list[dict]]
async def space_has_ready_documents(space_id) -> bool
canonical_document_ids = pipeline.chat.canonical_card_ids   # same sorted-JSON canonical form
```

`to_document_response(row, *, current_backend)` builds the API shape (4.8),
including `source_state` and `needs_reembed`.

### 4.6 Linking (`documents/linking.py`)

Move `_IDENTIFIER_RE` from `pipeline/trace.py` to `laya/retrieval.py` as
`IDENTIFIER_RE` and import it back in trace (`from laya.retrieval import IDENTIFIER_RE as _IDENTIFIER_RE`),
so the shared primitive has one home. Add a stricter regex for document text:

```python
DOC_IDENTIFIER_RE = re.compile(
    r"\b([A-Z][A-Z0-9]{1,9})[-#](\d{1,6})\b"          # PROJ-123, ABC#42
    r"|\b(PR|MR|GH|ISSUE)\s?#?(\d{1,6})\b"            # PR 540, PR #540, GH-12
)
```

`extract_identifiers(text) -> list[tuple[str, int | None]]` returns distinct
identifiers with the first page they appear on.

`resolve_entity_links(db, space_id, text, identifiers) -> list[dict]`:

1. For each identifier build the same variants `trace._identifier_search`
   builds (`PROJ-123`, `PROJ 123`, `PROJ123`, `#123` where applicable) and run
   `SELECT DISTINCT entity_id FROM action_cards WHERE (source_ref LIKE ? OR entity_id LIKE ? OR header LIKE ?) AND space_id = ?`
   → `link_method="identifier"`, `confidence=1.0`.
2. `SELECT entity_id FROM entities WHERE platform_refs LIKE '%"<identifier>"%'`
   → `entity_ref`, `0.9`.
3. Mentions: `SELECT entity_id, canonical_name FROM entities ORDER BY updated_at DESC LIMIT 500`;
   names of 4+ characters matched case-insensitively as substrings of the
   text → `mention`, `0.6`, `mention_count` = occurrences.

Dedupe on `entity_id` keeping the highest confidence; cap 100 links per
document. `entity_refs` written to Chroma metadata is the CSV of linked
entity ids (max 20).

### 4.7 Ingest lifecycle (`documents/ingest.py`)

```python
_ingest_sem = asyncio.Semaphore(1)            # one document embeds at a time
_running: dict[str, asyncio.Task] = {}

async def create_from_bytes(space_id, filename, stream, *, title=None, source_path=None, content_type=None) -> dict
async def create_from_path(space_id, path, *, title=None) -> dict
async def run_ingest(document_id, stages=("parse", "chunk", "embed", "link", "summarize")) -> None
async def recover_stalled_documents() -> None
async def delete_document_cascade(document_id) -> None
```

**Create** (both entry points):

1. Extension must be in `parsers.supported_extensions()` else HTTP 415
   (`error_code=unsupported`). Size must be at most
   `settings.documents.max_file_mb` else 413 (`too_large`); for multipart the
   size is enforced while streaming, for from-path by `stat()` first.
2. sha256 computed with 1 MiB reads (from the request body, or from the file
   at `source_path`).
   `SELECT document_id FROM documents WHERE space_id=? AND sha256=?` → HTTP 409
   with `{"detail": {"message": "...", "document_id": existing}}`.
3. `document_id = "doc_" + uuid4().hex[:12]`. `title` defaults to the
   filename stem.
   - **from-path** (`storage_mode='reference'`): no copy. Record
     `source_path` (expanded, absolute) and `source_mtime = stat().st_mtime`.
     The path must be a regular file the engine can read; symlinks are
     followed. The engine never writes to it.
   - **multipart** (`storage_mode='copy'`): write to a temp file inside
     `DOCUMENTS_DIR`, then `os.replace` to `DOCUMENTS_DIR/<document_id>.<ext>`
     after the INSERT succeeds; `stored_path` set, `source_path` NULL.
     Extension sanitised the way `cards_agent.py` does (alphanumeric, max 8).
4. INSERT `documents` with `status='queued'`, `created_at=updated_at=db_now()`,
   commit, broadcast `document_updated`.
5. `laya.tasks.create_task(run_ingest(document_id), name=f"doc_ingest_{document_id}")`,
   stored in `_running`.

**run_ingest** state machine. Each transition is `set_status` (UPDATE +
commit) followed by a `document_updated` broadcast with `progress`.

| Stage | Work | On failure |
|---|---|---|
| `parsing` | `path = store.readable_path(row)`; `parsed = await asyncio.to_thread(parser.parse, path)` | `SourceMissing` → `failed/source_missing` only on first ingest; on a reprocess of an already-ready document the chunks are kept and `error_code='source_missing'` is set without leaving `ready`. `ParseError` → `failed` with `error_code = e.error_code`, `error = str(e)`; stop. |
| `chunking` | `chunks = chunk_units(parsed.units)`; `replace_chunks` in a transaction; update `page_count`, `chunk_count`, `char_count`, and `title` if the parser found one and the user gave none | any → `failed/parse_error` |
| `embedding` | `async with _ingest_sem:` `delete_where({"document_id"})`, then `embed_batch` with `on_batch` broadcasting `{stage:"embedding", done, total}`; record `embedding_model` | `failed/embed_error`; Chroma tree deleted; chunks kept so `reprocess?stage=embed` can resume |
| `linking` | `extract_identifiers` + `resolve_entity_links` → `replace_links` | non-fatal: log `document_link_failed`, continue |
| `summarizing` | skipped with `error_code=summary_skipped_budget` if `budget.is_budget_paused()`; else `summarize_document` → `summary`, `summary_model`, then `embed_document(document_id, summary_text, summary_metadata)` | non-fatal: `error_code=summary_error`, `error=str(e)`, continue |
| `ready` | `indexed_at = db_now()`; `error`/`error_code` cleared unless a non-fatal code was set | |

`stages` lets `reprocess?stage=embed|summarize|link` run one stage from the
stored chunks without touching the original file. `stage=all` and re-sync
re-read the file at `readable_path`; re-sync additionally recomputes sha256
and `source_mtime` first and, when the hash is unchanged, returns early
without re-parsing. A `CancelledError` (engine shutdown or delete) marks the row
`failed/interrupted` in a `finally` block unless it is already terminal, so
`recover_stalled_documents()` (called from the lifespan after
`recover_stalled_events`) re-enqueues it. Every stage deletes-then-writes, so
re-running is idempotent.

**Delete cascade**, modelled on the card cascade in `api/cards_lifecycle.py`:

```python
task = _running.pop(document_id, None); if task: task.cancel()
async with transaction():
    DELETE FROM document_entity_links WHERE document_id = ?
    DELETE FROM document_chunks WHERE document_id = ?      -- per-row FTS trigger
    DELETE FROM documents WHERE document_id = ?
# outside the write lock
try: await asyncio.wait_for(delete_where({"document_id": document_id}), timeout=5.0)
except Exception: log.warning("document_vectors_delete_failed", document_id=...)
# file: only for storage_mode='copy'. os.path.normpath(stored_path) must be
# relative_to(DOCUMENTS_DIR) (same guard as cards_agent.py delete-staging), then
# best-effort unlink. A referenced source_path is NEVER touched.
await manager.broadcast({"type": "document_deleted", "document_id": ..., "space_id": ...})
```

Add `ERROR_CODES += ("source_missing",)`.

**Space delete**: in the `api/spaces_api.py` delete transaction add
`UPDATE documents SET space_id = 'default' WHERE space_id = ?`, and after the
transaction best-effort `update_document_metadata(doc_id, {"space_id": "default"})`
per moved document with a timeout, mirroring the card-move flow.

### 4.8 API (`engine/laya/api/documents_api.py`)

Register in `main.py` with the same `dependencies=auth_deps` as every other
router. Declare static paths (`/documents/supported-types`,
`/documents/by-entity`) before `/documents/{document_id}`.

| Method and path | Input | Output |
|---|---|---|
| `POST /documents` | multipart `file`, form `space_id="default"`, `title?` | 202 `DocumentResponse` (`queued`, `storage_mode=copy`); 409 duplicate `{detail:{message, document_id}}`; 413; 415 |
| `POST /documents/from-path` | `{path, space_id, title?}` | same with `storage_mode=reference`; 404 when the path is not a readable file. This is the path Tauri drops and the picker use. |
| `GET /documents` | `space_id?, status?, q?, limit=50, offset=0` | `{documents: [DocumentResponse], total}` |
| `GET /documents/supported-types` | | `{extensions: [...], max_file_mb}` |
| `GET /documents/by-entity` | `entity_id` | `[DocumentResponse + link_method, confidence]` |
| `GET /documents/{id}` | | `DocumentResponse` with `summary` and `entity_links` |
| `GET /documents/{id}/chunks` | `page_from?, page_to?, offset=0, limit=50` | `{chunks: [DocumentChunk], total}` |
| `GET /documents/{id}/related` | `entity_id?` | `{entities: [links], cards: [CardResponse]}` for `action_cards.entity_id IN links` |
| `GET /cards/{card_id}/related-documents` | | documents linked to the card's `entity_id` (lives in this router; does not collide with `cards_lifecycle`) |
| `PATCH /documents/{id}` | `{title}` | updated `DocumentResponse`; FTS trigger plus `update_document_metadata(id, {"title"})` |
| `POST /documents/{id}/reprocess` | `?stage=all\|embed\|summarize\|link` | 202; `stage=all` re-reads the file, 409 `source_missing` when it is gone |
| `POST /documents/{id}/resync` | | 202 when the source changed on disk (re-ingest from the path, same id, chunks and vectors replaced); 200 `{status: "unchanged"}` when the hash matches; 409 `source_missing` |
| `POST /documents/{id}/relocate` | `{path}` | updates `source_path`/`source_mtime` for a moved file after verifying its sha256 matches; 409 on mismatch |
| `DELETE /documents/{id}` | | `{status: "deleted"}`; never touches a referenced source file |

`DocumentResponse` (`models/document.py`):

```python
class DocumentResponse(BaseModel):
    document_id: str; space_id: str; title: str; original_filename: str; file_type: str
    mime_type: str | None; size_bytes: int; page_count: int | None; chunk_count: int; char_count: int
    storage_mode: Literal["reference", "copy"]; source_path: str | None
    source_state: Literal["copy", "ok", "changed", "missing"]
    status: str; error: str | None; error_code: str | None
    summary: str | None = None; summary_model: str | None = None
    embedding_model: str | None; needs_reembed: bool
    created_at: str; updated_at: str; indexed_at: str | None
    entity_links: list[DocumentEntityLink] = []

class DocumentChunk(BaseModel):
    chunk_id: str; document_id: str; chunk_index: int; text: str
    page_start: int | None; page_end: int | None; heading: str | None; char_count: int
```

WebSocket messages, broadcast via `laya.api.websocket.manager.broadcast`:

```json
{"type": "document_updated", "document_id": "doc_…", "space_id": "default", "status": "embedding",
 "title": "Q3 Roadmap", "progress": {"stage": "embedding", "done": 64, "total": 210}, "error": null, "error_code": null}
{"type": "document_deleted", "document_id": "doc_…", "space_id": "default"}
```

Multipart handling streams the body (`while chunk := await file.read(1 << 20)`)
updating the hash and size, aborting past the cap, into a temp file inside
`DOCUMENTS_DIR`. From-path handling mirrors the validation in
`upload_agent_file_path` (`expanduser`, `is_file`, size check) but stops
there: it hashes the file in place and records the path instead of copying.

### 4.9 Chat integration

**Models** (`models/chat.py`): `ChatRequest.document_ids: list[str] | None = None`;
`ChatMessage.referenced_documents: list[str] = []`; `ChatResponse.referenced_documents: list[str] = []`.

**Citations** (`pipeline/chat.py`, beside the card/event regexes):

```python
DOC_REF_PATTERN = re.compile(r"\[doc:([A-Za-z0-9_\-]+)(?:\s+p(?:age|\.)?\s*\d+(?:\s*[-–]\s*\d+)?)?\]")
# referenced_documents = list(dict.fromkeys(DOC_REF_PATTERN.findall(content)))
```

Persist in both paths: the REST INSERT of the assistant message and the
streaming UPDATE gain `referenced_documents`; `ChatMessage`/`ChatResponse`
construction and the `chat_stream_done` payload carry it;
`api/chat_api.py:get_chat_history` selects and hydrates it; the
`ws_router._handle_chat_message` error fallback includes `"referenced_documents": []`.

**Attachments**:

- `_ensure_conversation(db, ..., card_ids, document_ids=None)` stores
  `canonical_document_ids(document_ids)`.
- `_resolve_document_ids(db, conversation_id, requested) -> list[str]`: when
  `requested is not None`, `UPDATE chat_conversations SET document_ids=?` and
  return it; else read the stored column. Called right after
  `_ensure_conversation` in both `process_chat_message` and
  `process_chat_message_streaming`.
- `PUT /chat/conversations/{id}/documents {document_ids}` in `chat_api.py`
  attaches or detaches mid-conversation; the conversation list returns
  `document_ids`.
- `has_documents = bool(document_ids) or await store.space_has_ready_documents(space_id)`
  is computed once per turn and passed to both `build_chat_messages` and
  `select_chat_tools`.

**Retrieval**:

```python
skip_ambient = card_context or await _prev_turn_used_tools(conversation_id)
if skip_ambient and not document_ids:
    context = EMPTY
else:
    context = await _retrieve_context(user_message, space_id=space_id, document_ids=document_ids,
                                      token_budget=3500 if document_ids else 2000)
```

An explicit attachment is a stated focus and wins over the fetch-on-demand
heuristic. `_retrieve_context` adds two gather entries,
`_document_vector_search(user_message, space_id, n=10, document_ids)` and
`_document_keyword_search(user_message, space_id, n=10, document_ids)`, thin
wrappers over `documents/search.py`, and raises the post-RRF cut from 12 to
16 when documents are attached. Item shape:

```python
{"id": chunk_id,                      # equals the Chroma id so both signals fuse
 "document_id": doc_id, "chunk_id": chunk_id, "type": "document",
 "text": chunk_text,
 "metadata": {"title", "page_start", "page_end", "heading", "file_type", "space_id", "is_summary": False},
 "source": "doc_semantic" | "doc_keyword"}
```

A `document_summary` hit uses `id = document_id`, `chunk_id = None`,
`is_summary = True`. The vector `where` is
`and_where({"content_type": {"$in": DOCUMENT_CONTENT_TYPES}}, {"space_id": space_id} if space_id else None, {"document_id": {"$in": document_ids}} if document_ids else None)`
with `max_distance=0.60`. `_semantic_search` (cards) gets
`and_where(CARD_CONTENT_FILTER, {"space_id": ...})`.

`_format_context_item` gains a branch before the generic fallback:

```python
elif itype == "document":
    doc_id = item.get("document_id") or meta.get("document_id") or item.get("id", "?")
    title = meta.get("title", "")
    if meta.get("is_summary"):
        return f'[Doc {doc_id} "{title}" summary] {text}'
    p0, p1 = meta.get("page_start"), meta.get("page_end")
    page = f" p.{p0}" if p0 and (not p1 or p1 == p0) else (f" pp.{p0}-{p1}" if p0 else "")
    head = f" §{meta['heading']}" if meta.get("heading") else ""
    return f'[Doc {doc_id} "{title}"{page}{head}] {text}'
```

**Prompt** (`llm/prompts/chat.py:build_chat_messages(..., document_context=None, has_documents=False)`).
Gated on `has_documents` (token cost, P6 convention):

```
## Corpus Documents
The user's Corpus holds documents they shared (PDF, DOCX, Markdown, ...) indexed by page.
Retrieved context may include [Doc ID "Title" p.N] excerpts. Use list_documents,
search_documents and read_document to look deeper. When you cite a document, write
[doc:DOCUMENT_ID] or [doc:DOCUMENT_ID p.N] so the app can link it.
```

When `document_context` is set, an "## Attached Documents" block mirrors
Active Card Context: one line per document with id, title, type, page count
and the first 600 characters of its summary ("summary pending" when NULL),
capped at 5 documents or 3,000 characters, built by
`_build_document_context(db, document_ids)`.

### 4.10 Tools (`llm/tools/document_tools.py`, `definitions.py`, `executor.py`)

```python
async def list_documents(space_id=None, status="ready", limit=50, offset=0) -> dict
    # {documents: [{document_id, title, file_type, page_count, chunk_count, status, created_at, summary_preview}], total, has_more}
async def search_documents(query, document_id=None, limit=10, space_id=None) -> dict
    # hybrid_chunk_search → {results: [{document_id, title, chunk_id, chunk_index, page_start, page_end, heading, text[:800]}],
    #                        count, hint: "Cite as [doc:ID p.N]"}
async def read_document(document_id, page_from=None, page_to=None, chunk_from=None, chunk_to=None,
                        max_chars=10000, space_id=None) -> dict
    # page range → chunks whose [page_start, page_end] intersects; chunk range → by index; default: first 8 chunks
    # {document_id, title, summary (when no range given), chunks: [...], has_more, next_chunk_index, total_chunks, page_count}
```

`constants.py`: `DOCUMENT_SEARCH_DEFAULT = 10`, `DOCUMENT_SEARCH_MAX = 50`,
`DOCUMENT_LIST_MAX = 200`, `DOCUMENT_READ_MAX_CHARS = 12000` (matches the
tool-result cap).

`definitions.py`: a `_document_tools()` group with the three definitions,
added to `get_all_tool_definitions()` and to `read_tool_names()`. Because
`mcp/scope.py` derives its sets from those functions and
`mcp/http_server.py` filters `get_all_tool_definitions()` by scope, MCP
exposure under the read scope needs no MCP code change. Gating:

```python
_DOCUMENT_HINTS = ("document", "doc ", "docs", "pdf", "docx", "file", "spec", "report", "library", "corpus",
                   "attached", "attachment", "page ", "pages", "read the", "contract", "proposal",
                   "whitepaper", "paper", "manual", "handbook", "policy", "prd", "rfc", "design doc",
                   "slide", "spreadsheet", "csv")
def select_tool_definitions(text, *, has_documents=False): ... if has_documents or _hits(t, _DOCUMENT_HINTS): defs += _document_tools()
def select_chat_tools(user_message, chat_history=None, *, has_documents=False)
```

`executor.py:_register_tools` maps the three names to the handlers.

### 4.11 Trace (`pipeline/trace.py`, `models/trace.py`, `api/trace_api.py`)

- `_semantic_search`: `where = and_where(CARD_CONTENT_FILTER, {"space_id": ...})`
  and drop any hit without `metadata.card_id`.
- `models/trace.py`: `TraceDocumentRef(document_id, title, file_type, page_count, link_method, confidence, entity_ids: list[str], first_page)`;
  `TraceCluster.documents: list[TraceDocumentRef] = []`;
  `TraceResponse.documents: list[TraceDocumentRef] = []` for identifier-only matches.
- In `_run_trace_inner`, after `_expand_seeds` and the relevance filter:
  `doc_links = await store.documents_for_entities({c.entity_id for c in all_cards} | {s["entity_id"] for s in seeds if s.get("entity_id")}, space_id)`.
  Also match `IDENTIFIER_RE.findall(request.query)` against
  `document_entity_links.matched_text` so "trace PR-540" surfaces the PRD
  mentioning PR-540 even when no card does.
- `_build_clusters(all_cards, entity_map, seeds, doc_links=None)`: after
  cards are grouped, `cluster.documents = dedupe(doc for eid in cluster_entity_ids for doc in doc_links.get(eid, []))`.
  It runs in the executor with the precomputed dict; no DB access inside.
- `_save_trace`: each cluster entry in `cluster_data` gains `"documents": [...]`;
  `response.documents` goes to the new `traces.documents` column.
  `_reconstruct_trace` hydrates both.

### 4.12 Summary (`llm/prompts/document_summary.py`, `documents/summary.py`)

Prompt module, one per role as elsewhere, override-able via `get_prompt`:

```python
DOCUMENT_MAP_SYSTEM_PROMPT     # summarise ONE section; JSON {summary, key_points[], identifiers[], people[]}
DOCUMENT_REDUCE_SYSTEM_PROMPT  # merge section summaries of one document; markdown ≤ 300 words with
                               # headings Overview / Key points / Decisions & action items / Identifiers
def build_document_map_messages(title, file_type, part_index, part_total, text, page_range) -> list[dict]
def build_document_reduce_messages(title, file_type, page_count, partials: list[dict]) -> list[dict]
def get_document_map_schema() -> dict
def get_document_reduce_schema() -> dict     # {"summary": str, "key_points": [str], "identifiers": [str]}
```

Driver `summarize_document(document_id, chunks, title, file_type, page_count, space_id) -> dict`:

- Windows: consecutive chunks packed to `settings.documents.summary_window_chars`
  (12,000 chars, roughly 3K tokens). One window → a single reduce-style call.
- Cap `summary_max_windows` (24): beyond it keep the first 12, the last 6 and
  6 evenly sampled, and note "(summary covers a sample of N sections)".
- Map calls under `asyncio.Semaphore(2)`:
  `llm_call(role=ROLE, messages=..., response_schema=map_schema, step="document_summary_map", temperature=0.2, max_tokens=1024, space_id=space_id)`.
- Reduce hierarchically in groups of 8 when the partials exceed a window;
  `step="document_summary_reduce"`.
- `ROLE = "document_summary"` when `settings["models"].get("document_summary")`
  is set, else `"stager"` so the per-space stager override applies. Add
  `"document_summary": "stager"` to `_ROLE_FALLBACKS` in `llm/client.py`.
- Budget: add `"document_summary_map": "Corpus"` and
  `"document_summary_reduce": "Corpus"` to `STEP_TO_FEATURE` in
  `pipeline/budget.py`; skip when `is_budget_paused()`.

### 4.13 Dependencies (`engine/requirements.txt`)

```
# Documents (Corpus parsers). Pure Python; every pin has an upper bound.
pypdf>=6.0.0,<7      # per-page text; AES-encrypted PDFs use cryptography, already in the lock
mammoth>=1.9.0,<2    # DOCX → Markdown; python-docx would pull lxml, a native wheel not in the lock
```

Then:

```bash
scripts/lock-deps.sh
scripts/check-locks.sh -v
scripts/smoke-install.sh
scripts/smoke-install.sh --ml      # once, to confirm the real embed path
cd engine && source .venv/bin/activate && pytest
```

`requirements-dev.txt` needs nothing new: test fixtures generate PDFs with
pypdf's `PdfWriter`; a two-page text PDF and a minimal `.docx` are checked in
under `engine/tests/fixtures/documents/`.

---

## 5. UI

### 5.1 Types and client

`ui/src/lib/api/types.ts`: `CorpusDocument` (mirror of `DocumentResponse`, including `storage_mode` and `source_state`),
`DocumentChunk`, `DocumentStatus` union, `DocumentEntityLink`;
`ChatMessage.referenced_documents: string[]`; `Conversation.document_ids?: string[]`;
`TraceCluster.documents?: TraceDocumentRef[]`; `TraceResponse.documents?`.

`ui/src/lib/api/engine.ts` (all through `request`; multipart like `uploadAgentFile`):

```ts
uploadDocument(file: File, spaceId: string, title?: string)
uploadDocumentPath(path: string, spaceId: string, title?: string)
listDocuments(params?: { space_id?: string; status?: string; q?: string; limit?: number; offset?: number })
getDocument(id); getDocumentChunks(id, params?); renameDocument(id, title); deleteDocument(id)
reprocessDocument(id, stage?: 'all' | 'embed' | 'summarize' | 'link'); resyncDocument(id); relocateDocument(id, path)
getRelatedDocuments(cardId); getDocumentsByEntity(entityId)
getSupportedDocumentTypes(); setConversationDocuments(conversationId, documentIds)
```

### 5.2 Stores and reducers

- `lib/stores/documents.ts`: `documents` writable keyed by space, `loadDocuments(spaceId)`,
  and a subscription to `lastMessage` that applies `document_updated` /
  `document_deleted` through a pure, unit-tested reducer in
  `lib/documents/documentUpdateReducer.ts` (same approach as
  `lib/feed/cardUpdateReducer.ts`). Initialised from the root layout like
  `chatStream.ts`, so progress survives panel close.
- `lib/stores/chat.ts`: `chatDocumentIds = writable<string[] | null>(null)`
  mirroring `chatCardIds`; cleared in `goBackToList` and `handleClose`.
  `ChatSidebar.send()` adds `document_ids` to the WS payload and the REST
  fallback. Opening a conversation seeds it from `Conversation.document_ids`.

### 5.3 Drag-drop anywhere

Extract the two mechanisms in `RunAgentModal.svelte` (~L255-299) into
`lib/utils/fileDrop.ts`:

```ts
export function subscribeFileDrop(h: {
  onOver?: () => void; onLeave?: () => void;
  onFiles?: (files: File[]) => void;      // HTML5 drop (browser / Vite dev)
  onPaths?: (paths: string[]) => void;    // Tauri native drop (macOS WKWebView delivers paths, not File blobs)
}): () => void
```

It registers the window-level `dragover`/`drop`/`dragleave` listeners and,
when running inside Tauri, `getCurrentWebviewWindow().onDragDropEvent`, and
returns a combined unsubscribe. `RunAgentModal` uses it while open and keeps
priority. `+layout.svelte` subscribes when the modal is closed and opens
`AddDocumentSheet` with the dropped files or paths. A full-window drop
overlay ("Add to Corpus") shows while dragging. Inside Tauri the drop yields
paths and goes through `uploadDocumentPath` (reference mode); in a plain
browser it yields `File` blobs and goes through `uploadDocument` (copy mode).

### 5.4 Components (`lib/components/corpus/`)

- `AddDocumentSheet.svelte`: space picker (default `default`, or the single
  space selected in `feedFilters.spaceFilter` when exactly one is selected),
  optional title, a tile per file with status and progress from the documents
  store, inline errors from the API (`no_text` → "scanned PDF, OCR not
  supported", `too_large`, `unsupported`, 409 duplicate with a link to the
  existing document). Files are submitted immediately on drop; the sheet is
  the progress view. When every tile is ready the sheet offers "Ask about
  these" (attach all and open chat) and "Open Corpus".
- `CorpusList.svelte`: documents grouped by space with multi-select
  checkboxes, status chip, page and chunk counts, summary preview, and a
  source-state badge (`ok` shows nothing; `changed` shows "Changed on disk"
  with a Re-sync action; `missing` shows "Source moved" with a Locate action
  that opens the file picker and calls `relocateDocument`; `copy` shows
  "Stored copy"). Row actions: open, rename, reprocess, delete (confirm).
  A sticky action bar appears when one or more rows are selected: "Ask about
  N documents" (sets `chatDocumentIds`, starts a new conversation, opens the
  chat sidebar), "Delete". A `needs_reembed` hint with a re-embed action
  shows after an embedding model switch.
- `DocumentView.svelte`: title, summary, entity links with card counts,
  extracted chunks in order with page markers; `?page=N` scrolls to and
  highlights the first chunk whose page range covers N. Header actions: "Ask
  about this", rename, reprocess, re-sync, "Reveal in Finder" (Tauri
  `opener` plugin; omitted in the browser), delete. Rendering the original
  file is Phase 2.

### 5.5 Route and navigation

New route `ui/src/routes/corpus/+page.svelte` hosting `CorpusList` and
`DocumentView` (`?doc=ID&page=N`), with a "Corpus" link beside Pulse, Omni
and Coherence in `+layout.svelte`, using an SVG icon consistent with the
others (a stacked-pages glyph). The Corpus page also accepts drops directly
and shows an empty state that explains the drop-anywhere gesture.

### 5.6 Chat sidebar and messages

- Composer gains a paperclip button (SVG, disabled while sending, never
  hidden) that opens a small popover listing the space's ready documents to
  attach, plus "Add file" which triggers an `<input type="file">`; drops onto
  the sidebar while it is open add and attach in one step. Attached-document
  chips render above the textarea with a remove control and call
  `setConversationDocuments` when the conversation exists.
- `ChatMessage.svelte:replaceMarkers` adds `[doc:ID]` and `[doc:ID p.N]`
  (same regex family as the engine's `DOC_REF_PATTERN`) rendered as a
  document glyph button with the title on hover, navigating to
  `/corpus?doc=ID&page=N`. Stale ids fail gracefully like card links do.
- `ChatConversationList.svelte` shows a small document-count chip on
  conversations that have `document_ids`, so focused conversations are
  recognisable in the list.

### 5.7 Card detail and Coherence

- `CardDetail.svelte`: a "Related documents" section beside Metadata, loaded
  from `getRelatedDocuments(card.card_id)` when the card has an `entity_id`;
  hidden when empty; each row links to the Corpus page.
- Coherence cluster view: render `cluster.documents` as chips under the
  cluster header, and `trace.documents` as a "Documents" row when present.

### 5.8 Legal

`routes/legal`: add two sentences. Text extracted from Corpus documents is
sent to the configured LLM provider the same way event content is. Files
shared by drop or picker are read in place and never copied or modified; the
extracted text and vectors are stored under `~/.laya/data`, and only browser
uploads keep a copy under `~/.laya/data/documents`.

---

## 6. Ordered implementation steps

1. **Dependencies, migration, config.** `requirements.txt` pins and the lock
   scripts; `074_documents.sql`; `config.py` `DOCUMENTS_DIR`,
   `ensure_directories`, `DEFAULT_SETTINGS["documents"]`.
2. **Protect card consumers.** `CARD_CONTENT_FILTER`, `and_where`,
   `embed_batch`, `delete_where` in `chromadb_store.py`; apply the filter to
   the six readers. Shippable on its own.
3. **FTS, chunking, parsers.** `db/fts.py` DDL, triggers, backfill;
   `db/chunking.chunk_segments` and `documents/chunking.py`; the parser
   package.
4. **Store, linking, summary.** `documents/store.py`, `documents/linking.py`
   with the `IDENTIFIER_RE` move, `llm/prompts/document_summary.py`,
   `documents/summary.py`, `_ROLE_FALLBACKS`, `STEP_TO_FEATURE`.
5. **Ingest and API.** `documents/ingest.py`; `api/documents_api.py`;
   `main.py` include and `recover_stalled_documents`; `spaces_api.py` move.
6. **Chat, tools, trace.** `models/chat.py`, `documents/search.py`, the
   `chat.py` changes, `llm/prompts/chat.py` blocks, `chat_api.py` history and
   conversation endpoints, `ws_router.py` fallback; `document_tools.py`,
   gating, executor registry; trace models, clusters, persistence.
7. **UI.** Types, client, stores and reducer; `fileDrop.ts`;
   `AddDocumentSheet`, `CorpusList`, `DocumentView`, `/corpus` route and
   nav link; select-and-ask action bar; chat paperclip, chips, conversation
   list chip and citation links; CardDetail related documents; Coherence
   chips; legal sentences.
8. **Docs.** Update `CLAUDE.md`: the `documents/` package in Architecture,
   migration range `001-074`, `documents_fts` in the FTS bullet, the new
   router in the router count. Mark this file "implemented".

---

## 7. Tests

New files under `engine/tests/`:

- `test_chunking.py`: `chunk_text` golden output unchanged on a 5 KB text;
  `chunk_segments` tag propagation; `chunk_units` page_start/page_end/heading;
  tiny-section merge; heading prefix.
- `test_document_parsers.py`: registry parity (every module in `parsers/`
  registers exactly one `PARSER`; extensions unique) mirroring
  `test_egress_registry_parity.py`; fixtures for txt, md, csv, json, html;
  the checked-in two-page PDF; a blank-page PDF built in-test with
  `pypdf.PdfWriter` raising `NoExtractableText`; an encrypted PDF raising
  `EncryptedDocument`; the minimal `.docx`.
- `test_documents_api.py`: multipart upload with `httpx.AsyncClient(files=)`
  creates a copy under `DOCUMENTS_DIR`; from-path creates NO copy, records
  `source_path` and `source_mtime`, and the source file is byte-identical
  afterwards; 413 with a patched cap; 415; 409 dedupe; list, get, chunks,
  related, by-entity; `source_state` is `ok`, then `changed` after touching
  the file, then `missing` after moving it; `resync` returns `unchanged` on
  an identical hash and re-ingests on a changed file; `relocate` accepts a
  matching hash and rejects a different one; rename updates `documents_fts`;
  delete cascade removes rows, FTS rows and vectors, unlinks a stored copy,
  and leaves a referenced source untouched; reprocess `stage=embed` works
  with the source missing; reprocess `stage=all` with the source missing
  keeps the document `ready` with `error_code=source_missing`. `run_ingest`
  is patched with `AsyncMock` for the HTTP tests; one end-to-end test runs it
  with the vector helpers and `llm_call` mocked.
- `test_document_ingest.py`: status and broadcast sequence (patch
  `manager.broadcast`); embed failure keeps chunks; summary failure is
  non-fatal; budget-paused skip; `recover_stalled_documents` re-enqueues;
  cancellation marks `interrupted`.
- `test_document_linking.py`: `DOC_IDENTIFIER_RE` positives and negatives;
  links to `action_cards.entity_id` through the conftest card helper; entity
  mentions; the cap.
- `test_document_summary.py`: window packing, cap and sampling, single-window
  path, hierarchical reduce, role selection.
- `test_document_tools.py`: the three tools, including `read_document` page
  and chunk ranges and `has_more`, result size within the cap.

Additions to existing files: `test_fts.py` (trigger insert/delete, title
rename, FTS vs LIKE parity with `fts_ready` patched False);
`test_chat_api.py` and `test_integration_chat.py` (citations in both paths,
`document_ids` round trip, attachments force retrieval, `_format_context_item`
document branch, `_semantic_search` passes the `$nin` filter);
`test_tool_gating.py` (plain turn still read+write only; a document keyword
or `has_documents=True` ships the group; `read_tool_names()` includes the
three); `test_mcp_http.py` (`scope_of("search_documents") == "read"`);
`test_trace_api.py` (filter, dropped hits without `card_id`, cluster
documents, save/reconstruct round trip); `test_spaces_api.py` (space delete
moves documents to `default`).

`conftest.py`: `insert_test_document(db, document_id, space_id, title, chunks=[...])`
and a `mock_document_vectors` fixture patching `embed_batch`, `delete_where`
and `embed_document`.

UI: a vitest file for `documentUpdateReducer.ts`; `npx svelte-check` clean.

---

## 8. End-to-end verification

1. `cd engine && pytest`; `scripts/check-locks.sh -v`; `scripts/smoke-install.sh`.
2. `cd ui && npx svelte-check && npm test`.
3. `scripts/dev.sh`, then:
   - drop a multi-page PDF on the window; the sheet shows parsing →
     embedding progress → ready; the Corpus page lists it with page and chunk
     counts and a summary; `~/.laya/data/documents` contains no copy of it;
   - ask chat a question whose answer is on page 3; the reply carries a
     `[doc:ID p.3]` link that opens the Corpus page at page 3;
   - select two documents on the Corpus page and press "Ask"; a new
     conversation opens with two chips; ask "summarise these"; the reply uses
     both summaries and `read_document`; reopen the conversation from the
     list and confirm the chips persist;
   - edit the source file; the row shows "Changed on disk"; re-sync; the new
     text is searchable; move the file; the row shows "Source moved" and chat
     still answers from it; Locate it; the badge clears;
   - open a card whose Jira key appears in the document; "Related documents"
     lists it; a Coherence trace on that key shows the document chip;
   - from a Claude Code session with the Laya MCP server configured, call
     `search_documents` and `read_document`;
   - rename the document; chat citations show the new title; delete it; chat
     no longer cites it and the original file is untouched on disk;
   - drop a scanned PDF; the tile reports "scanned PDF, OCR not supported";
     drop the same PDF twice; the second reports the duplicate;
   - switch the embedding model in settings; the Corpus page shows the
     re-embed hint; reprocess with `stage=embed` clears it.

---

## 9. Risks and tuning notes

- The chat semantic gate (`max_distance=0.60`) was tuned for card text.
  Document chunks are longer and plainer; if recall is poor, tune the
  document search's own gate independently (it is a separate call) rather
  than the shared one.
- Embedding a 300-page PDF on CPU with mpnet takes tens of seconds. The
  batch-and-yield loop and the single-document semaphore keep card emits
  responsive; if emits still lag, lower `embed_batch_size`.
- mammoth drops images and most tables' structure; tables arrive as
  paragraphs. Acceptable for Phase 1; a table-aware DOCX path is a Phase 2
  parser improvement.
- `chat_conversations.document_ids` may reference deleted documents; the
  attachment resolver filters to existing rows and cleaning the column is a
  Phase 2 housekeeping task.
- The remote-engine deployment mode needs the multipart path; from-path is
  only valid when the engine and the UI share a filesystem. The UI uses
  from-path for Tauri native drops and the picker, and multipart otherwise,
  so remote engines always end up with a stored copy.
- Reference mode means the engine reads a file the user controls at ingest
  and again on reprocess or re-sync. Files on removable or network volumes
  will show as "missing" while unmounted; the document keeps working from
  SQLite and Chroma, and the badge clears when the volume returns. The
  engine never writes to a referenced path.
- The sha256 is only recomputed on re-sync. A file edited and restored to
  the same mtime (rare, but possible with sync tools) would not show as
  changed; re-sync still detects it by hash.
