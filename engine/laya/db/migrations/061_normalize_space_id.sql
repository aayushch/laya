-- Normalize space_id: NULL → 'default' for cards and events.
-- Processing rules keep NULL = "global" (applies to all spaces).
UPDATE action_cards SET space_id = 'default' WHERE space_id IS NULL;
UPDATE events SET space_id = 'default' WHERE space_id IS NULL;

-- Add FK from processing_rules.space_id → spaces(space_id).
-- NULL is still allowed (means global rule). Recreate table for constraint.
CREATE TABLE IF NOT EXISTS processing_rules_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    space_id        TEXT REFERENCES spaces(space_id) ON DELETE SET NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    position        INTEGER NOT NULL DEFAULT 0,
    condition_json  TEXT NOT NULL CHECK(json_valid(condition_json)),
    actions_json    TEXT NOT NULL CHECK(json_valid(actions_json)),
    rate_limit      INTEGER DEFAULT 0,
    cooldown_secs   INTEGER DEFAULT 0,
    max_daily       INTEGER DEFAULT 0,
    last_fired_at   DATETIME,
    fire_count      INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO processing_rules_v2
    SELECT * FROM processing_rules;

DROP TABLE IF EXISTS processing_rules;
ALTER TABLE processing_rules_v2 RENAME TO processing_rules;

CREATE INDEX IF NOT EXISTS idx_proc_rules_space ON processing_rules(space_id);
CREATE INDEX IF NOT EXISTS idx_proc_rules_enabled ON processing_rules(enabled);
