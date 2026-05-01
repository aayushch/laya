-- Entity-level agent association: add entity_id to workspace_sessions
-- and retire the requires_approval card status.

ALTER TABLE workspace_sessions ADD COLUMN entity_id TEXT;
CREATE INDEX IF NOT EXISTS idx_workspace_sessions_entity_id ON workspace_sessions(entity_id);

-- Existing requires_approval cards become ready (status no longer used)
UPDATE action_cards SET status = 'ready' WHERE status = 'requires_approval';
