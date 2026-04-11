-- Smart context grouping: semantic layer above entity_id that merges
-- related cards across entity and platform boundaries.

ALTER TABLE action_cards ADD COLUMN context_id TEXT;
CREATE INDEX idx_cards_context_id ON action_cards(context_id);

-- Metadata for each context group
CREATE TABLE context_groups (
    context_id      TEXT PRIMARY KEY,
    label           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_confirmed  BOOLEAN DEFAULT FALSE,
    user_split      BOOLEAN DEFAULT FALSE
);

-- Tracks which entity_ids belong to a context group (audit + undo)
CREATE TABLE context_group_members (
    context_id      TEXT NOT NULL REFERENCES context_groups(context_id),
    entity_id       TEXT NOT NULL,
    confidence      REAL DEFAULT 0.0,
    link_method     TEXT DEFAULT 'semantic',
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (context_id, entity_id)
);
