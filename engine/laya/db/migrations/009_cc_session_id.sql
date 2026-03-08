-- Add Claude Code session ID for conversation resumption.
-- This is the UUID from Claude Code's output (different from our internal session_id).
ALTER TABLE workspace_sessions ADD COLUMN cc_session_id TEXT;
