-- Add per-space coding agent override (NULL = use global default from settings.json)
ALTER TABLE spaces ADD COLUMN coding_agent TEXT;
