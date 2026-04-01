-- Add fuzzy_search flag to traces for history display
ALTER TABLE traces ADD COLUMN fuzzy_search INTEGER DEFAULT 0;
