-- Milestone 7: Chat message persistence
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      TEXT PRIMARY KEY,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    referenced_cards TEXT,                  -- JSON array of card_ids
    referenced_events TEXT,                 -- JSON array of event_ids
    context_used    TEXT,                   -- JSON: sources used for response
    model_used      TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_timestamp ON chat_messages(timestamp);
