-- Migration 022: Status lifecycle redesign
-- New statuses: ready, requires_approval, done
-- Removed statuses: approved → done, completed → done, staged → ready, executing → done

-- Add agent_prompt column for deferred agent execution (requires_approval cards)
ALTER TABLE action_cards ADD COLUMN agent_prompt TEXT;

-- Migrate existing statuses to new lifecycle
UPDATE action_cards SET status = 'done' WHERE status = 'approved';
UPDATE action_cards SET status = 'done' WHERE status = 'completed';
UPDATE action_cards SET status = 'done' WHERE status = 'executing';
UPDATE action_cards SET status = 'ready' WHERE status = 'staged';
