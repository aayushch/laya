-- Milestone 3: Entity resolution + Router output storage

-- Store Router classification output on events
ALTER TABLE events ADD COLUMN router_output TEXT;

-- Cross-platform entity resolution
CREATE TABLE entities (
    entity_id       TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,    -- person | project | ticket | repo | thread | issue
    canonical_name  TEXT NOT NULL,    -- human-readable name
    platform_refs   TEXT NOT NULL,    -- JSON: {"jira": ["BUG-1234"], "bitbucket": ["PR-891"], ...}
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    link_method     TEXT,             -- explicit | semantic | llm_confirmed
    confidence      REAL DEFAULT 1.0  -- confidence of the link (1.0 for explicit)
);

CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_name ON entities(canonical_name);
