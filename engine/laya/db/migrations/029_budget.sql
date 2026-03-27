-- Budget control: monthly limits, paused-workflow tracking, and cost history.

-- Singleton budget configuration (only one row, id=1).
CREATE TABLE IF NOT EXISTS budget_config (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    monthly_limit_usd   REAL,                           -- NULL = no limit
    enabled             INTEGER NOT NULL DEFAULT 0,
    paused_at           TIMESTAMP,                      -- when budget pause was triggered
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed the singleton row so UPSERTs work from day one.
INSERT OR IGNORE INTO budget_config (id, enabled) VALUES (1, 0);

-- Workflows that were automatically paused when budget was exceeded.
-- Only these are resumed when budget clears (avoids re-activating
-- manually-deactivated workflows).
CREATE TABLE IF NOT EXISTS budget_paused_workflows (
    workflow_id TEXT PRIMARY KEY,
    source_id   TEXT,
    space_id    TEXT,
    paused_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Monthly cost snapshots — one row per model per calendar month.
-- Current month is computed live from audit_log; this stores history.
CREATE TABLE IF NOT EXISTS monthly_costs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month      TEXT NOT NULL,       -- 'YYYY-MM', e.g. '2026-03'
    model_used      TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    UNIQUE(year_month, model_used)
);

CREATE INDEX IF NOT EXISTS idx_monthly_costs_month ON monthly_costs(year_month);
