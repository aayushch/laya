-- Chat conversations: group chat messages into distinct conversations
CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT 'New Chat',
    space_id        TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_conversations_updated
    ON chat_conversations(updated_at DESC);

-- Add conversation_id to existing chat_messages
ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT
    REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
    ON chat_messages(conversation_id, timestamp);

-- Migrate existing messages into a single legacy conversation
INSERT INTO chat_conversations (conversation_id, title, created_at, updated_at)
    SELECT 'legacy_chat', 'Previous Chat',
           COALESCE(MIN(timestamp), CURRENT_TIMESTAMP),
           COALESCE(MAX(timestamp), CURRENT_TIMESTAMP)
    FROM chat_messages
    WHERE EXISTS (SELECT 1 FROM chat_messages LIMIT 1);

UPDATE chat_messages SET conversation_id = 'legacy_chat'
    WHERE conversation_id IS NULL
    AND EXISTS (SELECT 1 FROM chat_messages LIMIT 1);
