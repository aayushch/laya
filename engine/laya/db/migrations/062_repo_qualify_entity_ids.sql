-- Repo-qualify entity_ids for GitHub and Bitbucket to prevent cross-repo collisions.
-- Old format: "github:pull_request:42" or "bitbucket:pull_request:PR-69"
-- New format: "github:pull_request:owner/repo/#42" or "bitbucket:pull_request:ws/repo/PR-69"
-- Repo context extracted from events.content_metadata ($.repo / $.bb_repository).

-- GitHub: prepend metadata.repo + '/#' to the existing subject_id
UPDATE action_cards
SET entity_id = (
    SELECT
        e.source_platform || ':' || e.subject_type || ':' ||
        json_extract(e.content_metadata, '$.repo') || '/' || e.subject_id
    FROM events e
    WHERE e.event_id = action_cards.event_id
)
WHERE entity_id LIKE 'github:%'
  AND entity_id NOT LIKE '%/%'
  AND EXISTS (
    SELECT 1 FROM events e2
    WHERE e2.event_id = action_cards.event_id
      AND json_extract(e2.content_metadata, '$.repo') IS NOT NULL
  );

-- Bitbucket: prepend metadata.bb_repository + '/' to the existing subject_id
UPDATE action_cards
SET entity_id = (
    SELECT
        e.source_platform || ':' || e.subject_type || ':' ||
        json_extract(e.content_metadata, '$.bb_repository') || '/' || e.subject_id
    FROM events e
    WHERE e.event_id = action_cards.event_id
)
WHERE entity_id LIKE 'bitbucket:%'
  AND entity_id NOT LIKE '%/%'
  AND EXISTS (
    SELECT 1 FROM events e2
    WHERE e2.event_id = action_cards.event_id
      AND json_extract(e2.content_metadata, '$.bb_repository') IS NOT NULL
  );

-- Regenerate group summaries for affected entities (they'll rebuild on next access)
DELETE FROM group_summaries
WHERE entity_id LIKE 'github:%' OR entity_id LIKE 'bitbucket:%';
