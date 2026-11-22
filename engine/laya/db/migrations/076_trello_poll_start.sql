-- Copyright 2026 Aayush Chawla
-- SPDX-License-Identifier: Apache-2.0

-- Existing Trello connections can resume their first poll from source creation.
WITH trello_starts AS (
    SELECT 'trello-config:' || workflow_id AS config_key,
           strftime('%Y-%m-%dT%H:%M:%SZ', created_at) AS poll_start
    FROM sources
    WHERE platform = 'trello' AND source_type = 'ingestion'
)
UPDATE metadata
SET value = json_insert(value, '$.pollStartAt', (
    SELECT poll_start FROM trello_starts WHERE config_key = metadata.key
))
WHERE space_id = 'default'
  AND key IN (SELECT config_key FROM trello_starts WHERE poll_start IS NOT NULL)
  AND json_valid(value);
