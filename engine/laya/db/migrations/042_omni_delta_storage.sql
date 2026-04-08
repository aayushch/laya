-- Delta storage for Omni snapshots.
-- Incremental snapshots store only the diff (is_delta=1), resynthesis snapshots
-- store the full structure (is_delta=0) and serve as base checkpoints.
-- Existing rows default to is_delta=0 (full snapshot) — no data migration needed.
ALTER TABLE omni_snapshots ADD COLUMN is_delta INTEGER NOT NULL DEFAULT 0;
ALTER TABLE omni_snapshots ADD COLUMN base_version INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_omni_space_base
ON omni_snapshots(space_id, base_version);
