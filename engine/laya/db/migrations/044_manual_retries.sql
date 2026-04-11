-- Track how many times a user has manually retried a dead event
ALTER TABLE events ADD COLUMN manual_retries INTEGER NOT NULL DEFAULT 0;
