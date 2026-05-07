-- Per-space daily summaries: one summary row per (date, space_id) instead of one global row per date.
DROP TABLE IF EXISTS daily_summaries;

CREATE TABLE daily_summaries (
    date     TEXT NOT NULL,
    space_id TEXT NOT NULL DEFAULT 'default',
    summary_json TEXT NOT NULL,
    card_ids TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, space_id)
);
