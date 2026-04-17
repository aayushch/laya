-- Store the canonical set of card IDs a chat conversation is anchored to.
-- Used by the Omni → View Cards view so that returning to the same card set
-- restores the previous conversation instead of starting a fresh one.
-- Stored as a sorted JSON array string for stable equality comparisons.
ALTER TABLE chat_conversations ADD COLUMN card_ids TEXT;

CREATE INDEX IF NOT EXISTS idx_chat_conv_card_ids
    ON chat_conversations(card_ids)
    WHERE card_ids IS NOT NULL;
