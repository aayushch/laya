-- Egress connections: tracks platform credentials managed by the Connection Broker.
-- Actual secrets are stored in OS keychain; this table holds metadata only.

CREATE TABLE IF NOT EXISTS egress_connections (
    connection_id       TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    name                TEXT NOT NULL,
    n8n_credential_id   TEXT,
    space_id            TEXT,
    status              TEXT NOT NULL DEFAULT 'connected',
    capabilities        TEXT,          -- JSON array of action_type strings
    error_message       TEXT,
    last_validated_at   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_egress_connections_platform ON egress_connections(platform);
CREATE INDEX IF NOT EXISTS idx_egress_connections_space ON egress_connections(space_id);
