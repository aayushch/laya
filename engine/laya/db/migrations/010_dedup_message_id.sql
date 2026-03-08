-- Add agent_message_id for deduplication of replayed messages during --resume
ALTER TABLE workspace_events ADD COLUMN agent_message_id TEXT;
CREATE UNIQUE INDEX idx_workspace_events_agent_message_id
    ON workspace_events(session_id, agent_message_id)
    WHERE agent_message_id IS NOT NULL;
