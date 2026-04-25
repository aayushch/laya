-- Re-create context_group_members with card_id instead of entity_id.
-- The related-cards feature needs card-level granularity for linking/unlinking.
-- ON DELETE CASCADE auto-cleans rows when cards are deleted.
DROP TABLE IF EXISTS context_group_members;

CREATE TABLE context_group_members (
    context_id      TEXT NOT NULL REFERENCES context_groups(context_id),
    card_id         TEXT NOT NULL REFERENCES action_cards(card_id) ON DELETE CASCADE,
    confidence      REAL DEFAULT 0.0,
    link_method     TEXT DEFAULT 'semantic',
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (context_id, card_id)
);
