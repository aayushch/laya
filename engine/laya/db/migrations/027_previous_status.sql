-- Rename pre_archive_status → previous_status so it can track the status
-- before ANY terminal transition (done, dismissed, archived), not just archive.
ALTER TABLE action_cards RENAME COLUMN pre_archive_status TO previous_status;
