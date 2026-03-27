-- Add bookmark support to action cards
ALTER TABLE action_cards ADD COLUMN bookmarked_at DATETIME DEFAULT NULL;

CREATE INDEX idx_action_cards_bookmarked ON action_cards(bookmarked_at)
    WHERE bookmarked_at IS NOT NULL;
