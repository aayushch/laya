-- Add source_ref and source_url to action_cards for linking back to source.
-- source_ref: human-readable identifier (e.g. "#123", "PROJ-456", email subject)
-- source_url: direct URL back to the source item
ALTER TABLE action_cards ADD COLUMN source_ref TEXT;
ALTER TABLE action_cards ADD COLUMN source_url TEXT;

-- Backfill from linked events
UPDATE action_cards SET
    source_ref = (
        SELECT CASE source_platform
            WHEN 'github' THEN
                CASE subject_type
                    WHEN 'pull_request' THEN 'PR #' || subject_id
                    ELSE '#' || subject_id
                END
            WHEN 'jira' THEN subject_id
            WHEN 'gmail' THEN subject_title
            WHEN 'slack' THEN subject_title
            ELSE subject_id
        END
        FROM events WHERE events.event_id = action_cards.event_id
    ),
    source_url = (
        SELECT subject_url FROM events WHERE events.event_id = action_cards.event_id
    )
WHERE source_ref IS NULL;
