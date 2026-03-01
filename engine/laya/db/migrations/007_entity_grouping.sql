-- M9: Add entity_id to action_cards for grouping cards that refer to the same entity.
-- entity_id format: "platform:subject_type:subject_id" e.g. "jira:ticket:PROJ-123"
ALTER TABLE action_cards ADD COLUMN entity_id TEXT;

CREATE INDEX idx_action_cards_entity_id ON action_cards(entity_id);

-- Backfill entity_id for existing cards from their linked events
UPDATE action_cards SET entity_id = (
    SELECT source_platform || ':' || subject_type || ':' || subject_id
    FROM events
    WHERE events.event_id = action_cards.event_id
)
WHERE entity_id IS NULL;
