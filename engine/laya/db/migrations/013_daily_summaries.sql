-- Daily summaries table for Summary View feature
CREATE TABLE IF NOT EXISTS daily_summaries (
    date TEXT PRIMARY KEY,              -- ISO date e.g. '2026-03-10'
    summary_json TEXT NOT NULL,         -- structured JSON with sections
    card_ids TEXT NOT NULL DEFAULT '[]', -- JSON array of card_ids incorporated
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
