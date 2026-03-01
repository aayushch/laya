-- M8: Add retryable column to action_log for failed actions that can be retried.
ALTER TABLE action_log ADD COLUMN retryable BOOLEAN DEFAULT FALSE;
