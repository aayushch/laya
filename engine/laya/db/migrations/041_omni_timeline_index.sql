-- Index for efficient timeline range queries on generated_at
CREATE INDEX IF NOT EXISTS idx_omni_space_generated
ON omni_snapshots(space_id, generated_at DESC);
