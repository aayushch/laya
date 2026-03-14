-- Track which stage failed so retry can re-attempt the correct action.
-- Values: NULL (no failure), 'pipeline', 'agent_spawn', 'agent_execution', 'action_execution'
ALTER TABLE action_cards ADD COLUMN failed_stage TEXT;
