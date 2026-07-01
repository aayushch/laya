-- Copyright 2026 Aayush Chawla
-- SPDX-License-Identifier: Apache-2.0
--
-- Normalize every stored timestamp to the canonical DB format: space-separated
-- UTC `YYYY-MM-DD HH:MM:SS` (what SQLite's CURRENT_TIMESTAMP and db_ts()/db_now()
-- emit). Historically some writers used datetime.isoformat(), which stores a
-- `T` separator and a `+00:00` offset (and sometimes microseconds). Because these
-- columns are TEXT and compared lexicographically, a space (0x20) sorts before a
-- `T` (0x54), so a mixed-format column produced wrong range/sort results — most
-- visibly "chat can't find today's cards" (a same-day range matched zero rows).
--
-- All writers now go through laya/db/timeutil.py, so this one-time pass fixes the
-- legacy rows. Only rows that actually contain a `T` are touched; strftime()
-- reads the ISO value (incl. microseconds/offset), converts to UTC, and reformats.
-- COALESCE keeps the original value if strftime() can't parse it (never nulls data).

UPDATE action_cards      SET updated_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';
UPDATE action_cards      SET resolved_at       = COALESCE(strftime('%Y-%m-%d %H:%M:%S', resolved_at),       resolved_at)       WHERE resolved_at       LIKE '%T%';
UPDATE action_cards      SET read_at           = COALESCE(strftime('%Y-%m-%d %H:%M:%S', read_at),           read_at)           WHERE read_at           LIKE '%T%';
UPDATE action_cards      SET bookmarked_at     = COALESCE(strftime('%Y-%m-%d %H:%M:%S', bookmarked_at),     bookmarked_at)     WHERE bookmarked_at     LIKE '%T%';

UPDATE action_log        SET executed_at       = COALESCE(strftime('%Y-%m-%d %H:%M:%S', executed_at),       executed_at)       WHERE executed_at       LIKE '%T%';

UPDATE events            SET timestamp             = COALESCE(strftime('%Y-%m-%d %H:%M:%S', timestamp),             timestamp)             WHERE timestamp             LIKE '%T%';
UPDATE events            SET processing_started_at = COALESCE(strftime('%Y-%m-%d %H:%M:%S', processing_started_at), processing_started_at) WHERE processing_started_at LIKE '%T%';
UPDATE events            SET next_retry_at         = COALESCE(strftime('%Y-%m-%d %H:%M:%S', next_retry_at),         next_retry_at)         WHERE next_retry_at         LIKE '%T%';

UPDATE daily_summaries   SET updated_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';

UPDATE traces            SET created_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';
UPDATE traces            SET updated_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';

UPDATE context_rules     SET created_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';
UPDATE context_rules     SET updated_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';

UPDATE classification_rules SET created_at     = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';
UPDATE classification_rules SET updated_at     = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';

UPDATE omni_snapshots    SET generated_at      = COALESCE(strftime('%Y-%m-%d %H:%M:%S', generated_at),      generated_at)      WHERE generated_at      LIKE '%T%';
UPDATE omni_snapshots    SET created_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';

UPDATE omni_pins         SET pinned_at         = COALESCE(strftime('%Y-%m-%d %H:%M:%S', pinned_at),         pinned_at)         WHERE pinned_at         LIKE '%T%';

UPDATE ingestion_errors  SET occurred_at       = COALESCE(strftime('%Y-%m-%d %H:%M:%S', occurred_at),       occurred_at)       WHERE occurred_at       LIKE '%T%';
UPDATE ingestion_errors  SET first_occurred_at = COALESCE(strftime('%Y-%m-%d %H:%M:%S', first_occurred_at), first_occurred_at) WHERE first_occurred_at LIKE '%T%';
UPDATE ingestion_errors  SET last_occurred_at  = COALESCE(strftime('%Y-%m-%d %H:%M:%S', last_occurred_at),  last_occurred_at)  WHERE last_occurred_at  LIKE '%T%';
UPDATE ingestion_errors  SET cleared_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', cleared_at),        cleared_at)        WHERE cleared_at        LIKE '%T%';

UPDATE chat_conversations SET created_at       = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';
UPDATE chat_conversations SET updated_at       = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';
UPDATE chat_messages     SET timestamp         = COALESCE(strftime('%Y-%m-%d %H:%M:%S', timestamp),         timestamp)         WHERE timestamp         LIKE '%T%';

UPDATE egress_connections SET created_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', created_at),        created_at)        WHERE created_at        LIKE '%T%';
UPDATE egress_connections SET updated_at        = COALESCE(strftime('%Y-%m-%d %H:%M:%S', updated_at),        updated_at)        WHERE updated_at        LIKE '%T%';
UPDATE egress_connections SET last_validated_at = COALESCE(strftime('%Y-%m-%d %H:%M:%S', last_validated_at), last_validated_at) WHERE last_validated_at LIKE '%T%';
