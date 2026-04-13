-- Store original group_active_at before context linking overwrites it.
-- On unlink, this value is restored so cards revert to their original
-- feed date instead of staying on the linking date.
ALTER TABLE action_cards ADD COLUMN pre_context_group_active_at DATETIME;
