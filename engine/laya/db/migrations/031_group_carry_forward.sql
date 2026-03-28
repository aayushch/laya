-- M31: Add group_active_at for entity group carry-forward across days.
-- When a new card arrives for an existing entity, all cards in the group
-- get their group_active_at updated to "now", bubbling the entire group
-- to the current day's feed.

ALTER TABLE action_cards ADD COLUMN group_active_at DATETIME;

-- Backfill: for each entity group, set to MAX(created_at) across the group
UPDATE action_cards SET group_active_at = (
    SELECT MAX(a2.created_at) FROM action_cards a2
    WHERE a2.entity_id = action_cards.entity_id
)
WHERE entity_id IS NOT NULL;

-- Singletons & NULLs: group_active_at = created_at
UPDATE action_cards SET group_active_at = created_at
WHERE group_active_at IS NULL;

CREATE INDEX idx_cards_group_active_at ON action_cards(group_active_at);
