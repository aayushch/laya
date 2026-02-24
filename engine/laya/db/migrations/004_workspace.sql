-- Milestone 4: Workspace sessions and events for coding agent state persistence

CREATE TABLE workspace_sessions (
    session_id      TEXT PRIMARY KEY,
    card_id         TEXT NOT NULL REFERENCES action_cards(card_id),
    agent_type      TEXT NOT NULL,
    status          TEXT DEFAULT 'starting',
    repo_path       TEXT,
    initial_prompt  TEXT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at    DATETIME,
    findings_json   TEXT,
    error_message   TEXT
);

CREATE INDEX idx_workspace_sessions_card_id ON workspace_sessions(card_id);
CREATE INDEX idx_workspace_sessions_status ON workspace_sessions(status);

CREATE TABLE workspace_events (
    event_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES workspace_sessions(session_id),
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    content         TEXT NOT NULL,
    requires_input  BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_workspace_events_session_id ON workspace_events(session_id);
CREATE INDEX idx_workspace_events_timestamp ON workspace_events(timestamp);
CREATE INDEX idx_workspace_events_requires_input ON workspace_events(requires_input);
