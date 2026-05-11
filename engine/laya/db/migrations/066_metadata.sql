CREATE TABLE IF NOT EXISTS metadata (
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    space_id  TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (key, space_id)
);
