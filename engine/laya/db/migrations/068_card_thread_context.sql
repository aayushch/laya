-- Add thread_context column to action_cards for Contextual BM25 (RAG Phase 2).
--
-- Phase 1 prepends a short thread-context blurb to a follow-up card's ChromaDB
-- embedding (see emit.py::_fetch_thread_context) so terse updates ("Approved.")
-- keep the semantic referent of their thread. Persisting the same blurb here lets
-- the FTS5/BM25 lexical index (built at startup in db/fts.py) match on it too, so
-- the lexical half of hybrid search benefits from the same referent restoration as
-- the vector half. NULL for first cards and pre-existing rows (forward-only; no
-- backfill, matching the embedding's snapshot-at-emit philosophy).
ALTER TABLE action_cards ADD COLUMN thread_context TEXT;
