-- Trace feature: persistent semantic search results with narrative
CREATE TABLE IF NOT EXISTS traces (
    trace_id        TEXT PRIMARY KEY,
    query           TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    narrative       TEXT,
    chapters        TEXT,           -- JSON: [{label, timestamp, card_ids}]
    cluster_data    TEXT,           -- JSON: serialized cluster metadata
    card_ids        TEXT,           -- JSON: ordered list of card_ids
    search_metadata TEXT,           -- JSON: {semantic_hits, fuzzy_hits, ...}
    space_id        TEXT            -- NULL = cross-space search
);
CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_traces_query ON traces(query);
