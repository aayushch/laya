-- Milestone 3: Audit log for LLM calls and processing steps

CREATE TABLE audit_log (
    log_id          TEXT PRIMARY KEY,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_id        TEXT,             -- may be null for non-event operations (chat, briefing)
    card_id         TEXT,
    step            TEXT NOT NULL,    -- ingest | rules | route | worker | stage | emit | execute | chat | briefing
    model_used      TEXT,             -- e.g., anthropic/claude-haiku-4-5-20251001
    processing_tier INTEGER,          -- 1, 2, or 3
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    error           TEXT,
    metadata        TEXT              -- JSON: additional context
);

CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_log_event_id ON audit_log(event_id);
CREATE INDEX idx_audit_log_step ON audit_log(step);
