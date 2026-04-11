-- Context association learning: tracks user link/unlink actions and
-- extracts rules to improve future context grouping accuracy.

CREATE TABLE context_corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id_a       TEXT NOT NULL,
    card_id_b       TEXT NOT NULL,
    header_a        TEXT,
    header_b        TEXT,
    summary_a       TEXT,
    summary_b       TEXT,
    platform_a      TEXT,
    platform_b      TEXT,
    action          TEXT NOT NULL,       -- 'link' or 'unlink'
    space_id        TEXT,
    processed       INTEGER NOT NULL DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_context_corrections_unprocessed ON context_corrections(processed, space_id);

CREATE TABLE context_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    space_id        TEXT,
    rule_text       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'learned',  -- 'learned' or 'manual'
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
