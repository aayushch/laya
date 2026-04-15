-- Add last_error column to action_cards so failed cards can display the error message
ALTER TABLE action_cards ADD COLUMN last_error TEXT;
