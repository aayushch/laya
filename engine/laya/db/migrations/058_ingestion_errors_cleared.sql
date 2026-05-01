-- Soft-delete column for ingestion errors. Users can "clear" individual errors
-- (or all visible errors) from the Settings -> Audit UI. Cleared rows are
-- excluded from the listing endpoint by default but remain in the table until
-- the housekeeping cron purges them by `last_occurred_at` age.
ALTER TABLE ingestion_errors ADD COLUMN cleared_at TIMESTAMP;

-- Surfacing queries filter on cleared_at IS NULL; index keeps that fast.
CREATE INDEX IF NOT EXISTS idx_ing_err_cleared ON ingestion_errors(cleared_at, last_occurred_at DESC);
