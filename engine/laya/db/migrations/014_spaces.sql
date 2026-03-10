-- Spaces: user-defined contexts that group event sources with model/key configs
CREATE TABLE IF NOT EXISTS spaces (
    space_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT,
    icon         TEXT DEFAULT '📁',
    color        TEXT DEFAULT '#F97316',
    router_model TEXT,          -- NULL = use global default
    stager_model TEXT,
    chat_model   TEXT,
    is_default   INTEGER DEFAULT 0,
    position     INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sources: maps n8n workflow IDs to spaces
CREATE TABLE IF NOT EXISTS sources (
    source_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    platform       TEXT NOT NULL,
    workflow_id    TEXT NOT NULL UNIQUE,
    space_id       TEXT NOT NULL DEFAULT 'default' REFERENCES spaces(space_id) ON DELETE SET NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-space API keys (actual keys stored in OS keychain, this table tracks references)
CREATE TABLE IF NOT EXISTS space_api_keys (
    space_id  TEXT NOT NULL REFERENCES spaces(space_id) ON DELETE CASCADE,
    provider  TEXT NOT NULL,
    key_ref   TEXT NOT NULL,
    PRIMARY KEY (space_id, provider)
);

-- Extend existing tables with space_id
ALTER TABLE events ADD COLUMN space_id TEXT;
ALTER TABLE action_cards ADD COLUMN space_id TEXT;

-- Seed the default space (cannot be deleted)
INSERT OR IGNORE INTO spaces (space_id, name, icon, color, is_default, position)
VALUES ('default', 'Default', '🏠', '#F97316', 1, 0);
