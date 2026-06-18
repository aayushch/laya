-- Index on processing_rule_firings.fired_at for the global firing-log query
-- (ORDER BY fired_at DESC) and the retention prune (WHERE fired_at < ?).
-- The existing indexes are all rule_id-leading, so neither served a global
-- time-ordered scan or an age-based delete.
CREATE INDEX IF NOT EXISTS idx_proc_firings_fired ON processing_rule_firings (fired_at);
