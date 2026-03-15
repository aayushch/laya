-- Track the status a card had before being archived, so unarchiving restores it.
ALTER TABLE action_cards ADD COLUMN pre_archive_status TEXT;