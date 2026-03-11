-- Add add_dirs column to workspace_sessions for persisting additional directories
-- passed via --add-dir (Claude Code) or --include-directories (Gemini CLI).
-- Stored as JSON array of path strings, e.g. '["/path/a", "/path/b"]'.

ALTER TABLE workspace_sessions ADD COLUMN add_dirs TEXT;
