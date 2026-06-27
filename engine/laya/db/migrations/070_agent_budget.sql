-- Agent inference backend usage-limit budgeting.
-- Agents bill against time-windowed usage limits (not $), so this is window-based with
-- auto-resume at the window reset — distinct from the monthly $ budget (budget_config).

-- Singleton runtime pause state for agent usage limits.
CREATE TABLE IF NOT EXISTS agent_budget_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    paused_at    TEXT,
    paused_until TEXT,           -- when auto-resume becomes eligible (window reset)
    paused_reason TEXT,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO agent_budget_state (id) VALUES (1);

-- Ingestion workflows deactivated by an agent-usage pause (so only these are resumed).
CREATE TABLE IF NOT EXISTS agent_budget_paused_workflows (
    workflow_id TEXT PRIMARY KEY,
    source_id   TEXT,
    space_id    TEXT
);

-- Latest native rate-limit signal scraped from an agent (Claude Code's rate_limit_event).
-- resets_at is a unix timestamp; status is the agent's allow/reject string.
CREATE TABLE IF NOT EXISTS agent_rate_limit_state (
    agent_id        TEXT PRIMARY KEY,
    status          TEXT,
    resets_at       INTEGER,
    rate_limit_type TEXT,
    raw_json        TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
