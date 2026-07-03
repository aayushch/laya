# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Database migration runner. Reads numbered .sql files and applies them in order."""

from pathlib import Path

import aiosqlite
import structlog

from laya.config import MIGRATIONS_DIR

log = structlog.get_logger()


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply any pending migrations from the migrations directory."""
    # Ensure schema_version table exists
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            migration_file TEXT NOT NULL
        )
        """
    )
    await db.commit()

    # Get current version
    async with db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cursor:
        row = await cursor.fetchone()
        current_version = row[0]

    # Find and sort migration files
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    applied = 0
    for migration_file in migration_files:
        # Extract version number from filename (e.g., 001_initial.sql -> 1)
        version = int(migration_file.stem.split("_")[0])

        if version <= current_version:
            continue

        log.info("applying_migration", version=version, file=migration_file.name)
        # Explicit UTF-8: Windows defaults to cp1252 via locale.getpreferredencoding(),
        # which rejects multibyte UTF-8 bytes (e.g. smart quotes) found in comments.
        sql = migration_file.read_text(encoding="utf-8")

        # Run the whole file AND the schema_version bump inside ONE transaction so
        # a mid-file failure rolls back cleanly. executescript otherwise runs in
        # autocommit, so a failure left the file half-applied and unrecorded — the
        # next startup re-ran it and died on the first non-idempotent statement, a
        # boot loop needing manual surgery (review §2 API — P4-13). Our migrations
        # contain no BEGIN/COMMIT or triggers, so this wrapping is safe.
        escaped_name = migration_file.name.replace("'", "''")
        script = (
            "BEGIN;\n"
            + sql
            + f"\nINSERT INTO schema_version (version, migration_file) "
            + f"VALUES ({version}, '{escaped_name}');\n"
            + "COMMIT;"
        )
        try:
            await db.executescript(script)
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
            log.error("migration_failed_rolled_back", version=version, file=migration_file.name)
            raise
        applied += 1

    if applied:
        log.info("migrations_complete", applied=applied, current_version=current_version + applied)
    else:
        log.info("migrations_up_to_date", version=current_version)
