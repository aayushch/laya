-- 018: Classification feedback — corrections log and user-defined classification rules

-- Log of user corrections to card classifications (priority, persona)
CREATE TABLE IF NOT EXISTS classification_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL,
    space_id TEXT,
    field TEXT NOT NULL,
    original_value TEXT NOT NULL,
    corrected_value TEXT NOT NULL,
    card_summary TEXT,
    category TEXT,
    platform TEXT,
    event_type TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES action_cards(card_id)
);

CREATE INDEX IF NOT EXISTS idx_corrections_platform ON classification_corrections(platform, event_type);
CREATE INDEX IF NOT EXISTS idx_corrections_created ON classification_corrections(created_at);

-- User-defined classification rules (natural language, injected into router prompt)
CREATE TABLE IF NOT EXISTS classification_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    space_id TEXT,
    field TEXT,
    rule_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
