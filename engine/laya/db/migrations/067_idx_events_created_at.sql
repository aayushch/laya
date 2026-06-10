-- Index on events.created_at for efficient time-series aggregation
-- (throughput + wait-time charts GROUP BY minute bucket)
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at);
