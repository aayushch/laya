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

        # Execute all statements in the migration file
        await db.executescript(sql)

        # Record the migration
        await db.execute(
            "INSERT INTO schema_version (version, migration_file) VALUES (?, ?)",
            (version, migration_file.name),
        )
        await db.commit()
        applied += 1

    if applied:
        log.info("migrations_complete", applied=applied, current_version=current_version + applied)
    else:
        log.info("migrations_up_to_date", version=current_version)
