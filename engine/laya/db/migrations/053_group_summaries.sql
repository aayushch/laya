-- Rolling summaries for entity-id card groups
CREATE TABLE IF NOT EXISTS group_summaries (
    entity_id      TEXT PRIMARY KEY,
    headline       TEXT NOT NULL,
    summary        TEXT NOT NULL,
    key_events     TEXT,
    current_status TEXT,
    pending_actions TEXT,
    card_ids       TEXT NOT NULL,
    card_count     INTEGER NOT NULL DEFAULT 0,
    space_id       TEXT DEFAULT 'default',
    model          TEXT,
    created_at     DATETIME DEFAULT (datetime('now')),
    updated_at     DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_group_summaries_space
    ON group_summaries(space_id);
