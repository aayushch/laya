-- Milestone 1: Initial schema
-- Creates: events, action_cards, action_log

CREATE TABLE events (
    event_id            TEXT PRIMARY KEY,
    timestamp           DATETIME NOT NULL,
    source_platform     TEXT NOT NULL,
    source_connection_id TEXT,
    source_raw_event_type TEXT NOT NULL,
    actor_name          TEXT,
    actor_email         TEXT,
    actor_handle        TEXT,
    actor_relationship  TEXT,
    subject_type        TEXT NOT NULL,
    subject_id          TEXT NOT NULL,
    subject_title       TEXT,
    subject_url         TEXT,
    content_body        TEXT,
    content_metadata    TEXT,
    raw_json            TEXT NOT NULL,
    processed           BOOLEAN DEFAULT FALSE,
    filtered            BOOLEAN DEFAULT FALSE,
    filter_rule         TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE INDEX idx_events_subject_id ON events(subject_id);
CREATE INDEX idx_events_source_platform ON events(source_platform);
CREATE INDEX idx_events_actor_email ON events(actor_email);
CREATE INDEX idx_events_processed ON events(processed);

CREATE TABLE action_cards (
    card_id             TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL REFERENCES events(event_id),
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    priority            TEXT NOT NULL,
    persona             TEXT NOT NULL,
    category            TEXT NOT NULL,
    header              TEXT NOT NULL,
    summary             TEXT NOT NULL,
    intelligence        TEXT,
    staged_output       TEXT,
    suggested_actions   TEXT,
    status              TEXT DEFAULT 'pending',
    privacy_tier        INTEGER DEFAULT 2,
    has_workspace       BOOLEAN DEFAULT FALSE,
    resolved_at         DATETIME,
    user_feedback       TEXT,
    feedback_type       TEXT,
    confidence          REAL,
    router_model        TEXT,
    stager_model        TEXT,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cards_status ON action_cards(status);
CREATE INDEX idx_cards_priority ON action_cards(priority);
CREATE INDEX idx_cards_persona ON action_cards(persona);
CREATE INDEX idx_cards_event_id ON action_cards(event_id);
CREATE INDEX idx_cards_created_at ON action_cards(created_at);

CREATE TABLE action_log (
    action_id           TEXT PRIMARY KEY,
    card_id             TEXT NOT NULL REFERENCES action_cards(card_id),
    action_type         TEXT NOT NULL,
    target_platform     TEXT NOT NULL,
    target_connection_id TEXT,
    payload             TEXT NOT NULL,
    executed_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    result_status       TEXT,
    result_data         TEXT,
    error_message       TEXT,
    modifications       TEXT
);

CREATE INDEX idx_action_log_card_id ON action_log(card_id);
CREATE INDEX idx_action_log_executed_at ON action_log(executed_at);
