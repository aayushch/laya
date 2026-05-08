-- Tags feature: global tag definitions + polymorphic assignments

CREATE TABLE IF NOT EXISTS tags (
    tag_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color     TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id      INTEGER NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN ('card', 'entity', 'context')),
    target_id   TEXT NOT NULL,
    assigned_by TEXT NOT NULL DEFAULT 'user',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tag_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_assignments_target ON tag_assignments(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_tag_assignments_tag ON tag_assignments(tag_id);

-- Seed system tags
INSERT OR IGNORE INTO tags (name, color, is_system) VALUES ('spam', '#EF4444', 1);
INSERT OR IGNORE INTO tags (name, color, is_system) VALUES ('phishing', '#DC2626', 1);
INSERT OR IGNORE INTO tags (name, color, is_system) VALUES ('automated', '#6B7280', 1);
