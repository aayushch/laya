-- 032: Add processed flag to classification_corrections for learned rule extraction

ALTER TABLE classification_corrections ADD COLUMN processed INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_corrections_unprocessed ON classification_corrections(processed, space_id);
