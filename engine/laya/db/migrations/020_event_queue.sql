-- Add event queue columns for reliable processing with retry support.
-- Replaces the boolean `processed` flag with a proper state machine.

ALTER TABLE events ADD COLUMN processing_status TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE events ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events ADD COLUMN last_error TEXT;
ALTER TABLE events ADD COLUMN next_retry_at DATETIME;

-- Backfill: existing processed events → 'completed', unprocessed → 'queued'
UPDATE events SET processing_status = 'completed' WHERE processed = 1;
UPDATE events SET processing_status = 'filtered' WHERE filtered = 1;
UPDATE events SET processing_status = 'queued' WHERE processed = 0 AND filtered = 0;

CREATE INDEX IF NOT EXISTS idx_events_processing_status ON events (processing_status);
CREATE INDEX IF NOT EXISTS idx_events_next_retry_at ON events (next_retry_at);
