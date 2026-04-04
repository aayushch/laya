-- Link sources (workflow instances) to specific egress connections.
-- Enables multiple connections per platform, each with its own cloned workflows.
ALTER TABLE sources ADD COLUMN connection_id TEXT;
CREATE INDEX idx_sources_connection ON sources(connection_id);
