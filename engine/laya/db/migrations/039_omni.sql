-- Omni: rolling cross-platform summary snapshots and pinned items.

CREATE TABLE IF NOT EXISTS omni_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    snapshot_type TEXT NOT NULL DEFAULT 'scheduled',  -- 'incremental' | 'scheduled' | 'manual'
    content_json TEXT NOT NULL,          -- structured JSON: sections array
    card_ids TEXT NOT NULL DEFAULT '[]', -- JSON array of card_ids incorporated
    events_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omni_space_version ON omni_snapshots(space_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_omni_space_type ON omni_snapshots(space_id, snapshot_type);

CREATE TABLE IF NOT EXISTS omni_pins (
    pin_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL DEFAULT 'default',
    item_text TEXT NOT NULL,
    source_card_ids TEXT NOT NULL DEFAULT '[]', -- JSON array
    platforms TEXT NOT NULL DEFAULT '[]',       -- JSON array
    pinned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omni_pins_space ON omni_pins(space_id);
