-- Processing rules: automated event→action wiring
CREATE TABLE IF NOT EXISTS processing_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    space_id        TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    position        INTEGER NOT NULL DEFAULT 0,
    condition_json  TEXT NOT NULL,
    actions_json    TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_proc_rules_space ON processing_rules(space_id);
CREATE INDEX IF NOT EXISTS idx_proc_rules_enabled ON processing_rules(enabled);

CREATE TABLE IF NOT EXISTS processing_rule_firings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER NOT NULL REFERENCES processing_rules(id) ON DELETE CASCADE,
    card_id         TEXT NOT NULL,
    entity_id       TEXT,
    event_id        TEXT,
    fired_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    actions_json    TEXT,
    results_json    TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_proc_firings_rule ON processing_rule_firings(rule_id, fired_at);
CREATE INDEX IF NOT EXISTS idx_proc_firings_entity ON processing_rule_firings(rule_id, entity_id, fired_at);
