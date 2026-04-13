-- Store the permission mode used when starting an agent session so that
-- resumed sessions can use the same mode (e.g. 'full' for bash access).
ALTER TABLE workspace_sessions ADD COLUMN permission_mode TEXT;
