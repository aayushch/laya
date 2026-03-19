-- Add paused flag to spaces for flow control (pause/unpause all source workflows)
ALTER TABLE spaces ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
