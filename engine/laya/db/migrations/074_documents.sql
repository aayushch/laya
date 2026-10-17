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
