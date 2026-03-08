-- Remove unique constraint on agent_message_id; duplicates from --resume
-- will be handled client-side during rendering instead.
DROP INDEX IF EXISTS idx_workspace_events_agent_message_id;
