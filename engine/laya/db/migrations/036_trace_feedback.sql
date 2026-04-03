-- Trace feedback: learn from user cluster removals to improve future searches
CREATE TABLE IF NOT EXISTS trace_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    query        TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    entity_title TEXT,
    platform     TEXT,
    action       TEXT NOT NULL DEFAULT 'removed',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trace_feedback_entity ON trace_feedback(entity_id);
CREATE INDEX IF NOT EXISTS idx_trace_feedback_query ON trace_feedback(query);
