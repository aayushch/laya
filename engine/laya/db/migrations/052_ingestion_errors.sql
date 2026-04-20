-- Captures node-level failures from n8n ingestion workflows, reported by the
-- shared "Laya - Error Handler" workflow wired via settings.errorWorkflow.
-- Populated by POST /ingestion-errors. A surfacing layer (UI banner / per-source
-- indicator) will read from this table in a follow-up milestone.
--
-- TODO(surfacing): add a retention cron that purges acknowledged rows older
-- than 30 days. Not in scope for the capture milestone — unbounded growth is
-- fine until we start surfacing in the UI.
CREATE TABLE IF NOT EXISTS ingestion_errors (
    error_id            TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL,          -- n8n $workflow.id of the failing workflow
    source_id           TEXT,                   -- resolved via sources.workflow_id; NULL if unresolvable
    space_id            TEXT,                   -- follows source_id; NULL if unresolvable
    platform            TEXT,                   -- gmail, jira, slack, ... (derived in n8n)
    workflow_name       TEXT,
    node_name           TEXT,                   -- n8n `lastNodeExecuted`
    node_type           TEXT,                   -- e.g. n8n-nodes-base.httpRequest
    error_name          TEXT,                   -- e.g. NodeOperationError, AxiosError
    error_message       TEXT,
    error_http_code     INTEGER,                -- populated when the failure is an HTTP call
    error_details       TEXT,                   -- JSON blob: full n8n error object + stack
    execution_id        TEXT,
    execution_url       TEXT,                   -- deep-link into n8n's execution viewer
    execution_mode      TEXT,                   -- trigger, manual, webhook, ...
    fingerprint         TEXT NOT NULL,          -- sha256(error_name|normalized_message)[:16] — coalesce key
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    first_occurred_at   TIMESTAMP NOT NULL,
    last_occurred_at    TIMESTAMP NOT NULL,
    occurred_at         TIMESTAMP NOT NULL,     -- n8n-reported timestamp of the first occurrence in this row
    acknowledged_at     TIMESTAMP,              -- nullable; set by surfacing layer
    resolved_at         TIMESTAMP,              -- nullable; optional explicit-resolve hook
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Read patterns
CREATE INDEX IF NOT EXISTS idx_ing_err_space_time ON ingestion_errors(space_id, last_occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_ing_err_source_time ON ingestion_errors(source_id, last_occurred_at DESC);
-- Coalescing lookup: (workflow, node, fingerprint) within the dedup window
CREATE INDEX IF NOT EXISTS idx_ing_err_coalesce ON ingestion_errors(workflow_id, node_name, fingerprint, last_occurred_at DESC);
-- Surfacing "active issues" queries
CREATE INDEX IF NOT EXISTS idx_ing_err_unack ON ingestion_errors(acknowledged_at, last_occurred_at DESC);
