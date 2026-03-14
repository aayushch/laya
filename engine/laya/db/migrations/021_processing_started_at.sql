-- Track when an event started processing so we can detect stale/hung events.
ALTER TABLE events ADD COLUMN processing_started_at DATETIME;
