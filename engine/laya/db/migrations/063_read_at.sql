-- Track when a user has read/viewed an action card.
-- NULL = unread; non-NULL = read (timestamp of first read).
ALTER TABLE action_cards ADD COLUMN read_at DATETIME DEFAULT NULL;

-- Mark all existing cards as read so the feed doesn't appear
-- entirely "new" after this migration.
UPDATE action_cards SET read_at = datetime('now');
