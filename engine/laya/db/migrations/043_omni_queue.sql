-- Persistent queue for Omni processing.
-- Cards are enqueued here atomically with the card INSERT/UPDATE in emit.py.
-- The omni pipeline polls this table instead of relying on in-memory state,
-- making it crash-safe: unprocessed rows survive engine restarts.
CREATE TABLE IF NOT EXISTS omni_queue (
    card_id    TEXT PRIMARY KEY,
    space_id   TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL
);
