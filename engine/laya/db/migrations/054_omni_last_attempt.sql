-- Watermark that advances whenever Omni resynthesis pulls cards, regardless of
-- LLM success. Without this, an LLM failure leaves the `since` cutoff pinned to
-- the prior successful snapshot, so cards pile up behind broken runs and
-- saturate the fetch cap on subsequent attempts.
CREATE TABLE IF NOT EXISTS omni_last_attempt (
    space_id        TEXT PRIMARY KEY,
    last_attempt_at TEXT NOT NULL
);
