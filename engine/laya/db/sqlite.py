# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Async SQLite connection management via aiosqlite."""

import asyncio
from contextlib import asynccontextmanager

import aiosqlite
import structlog

from laya.config import DB_PATH, ensure_directories

log = structlog.get_logger()

# Module-level connection reference
_db: aiosqlite.Connection | None = None

# Serializes multi-statement invariants against each other on the single shared
# connection (see transaction()). NOT a per-request/per-writer lock.
_invariant_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    """Get the current database connection. Raises if not connected."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call connect() first.")
    return _db


@asynccontextmanager
async def transaction():
    """Run a multi-statement invariant atomically-enough on the shared connection.

    Laya uses ONE shared aiosqlite connection, so there is no per-task
    transaction isolation: statements from concurrent tasks interleave in a
    single connection-wide transaction, and any task's ``commit()`` flushes every
    other task's pending writes. A multi-statement invariant (card cascade
    delete, space delete, context-group merge/unlink) could therefore be
    committed half-applied, and a mid-sequence failure left partial rows for the
    next ``commit()`` elsewhere to persist — there was no ``rollback()`` anywhere
    (review §2 API — P4-12).

    This context manager (a) serializes guarded invariants against each other via
    a module lock so two of them can't interleave, and (b) commits on success /
    rolls back on failure so a raising invariant undoes its own uncommitted work
    instead of leaking it into someone else's commit. It deliberately does NOT
    wrap every writer — true per-request isolation on a shared connection is out
    of scope and would need a connection pool.

    Yields the shared connection. Keep the body to DB statements only — it holds
    the write lock, so do not await slow non-DB work (network, subprocess) inside
    it; do that before or after the block.
    """
    db = await get_db()
    async with _invariant_lock:
        try:
            yield db
            await db.commit()
        except Exception:
            # Undo this invariant's own uncommitted statements rather than let the
            # next commit() elsewhere flush a half-applied invariant. On the shared
            # connection this also rolls back any unguarded writer's still-pending
            # work — an accepted trade-off for these rare, low-frequency ops, and
            # strictly safer than today's leak-partial-then-commit behavior.
            await db.rollback()
            raise


async def connect() -> aiosqlite.Connection:
    """Open the SQLite connection and enable WAL mode."""
    global _db
    ensure_directories()
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA busy_timeout=5000")
    log.info("sqlite_connected", path=str(DB_PATH))
    return _db


async def disconnect() -> None:
    """Close the SQLite connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        log.info("sqlite_disconnected")


async def is_healthy() -> bool:
    """Quick health check — run a trivial query."""
    try:
        db = await get_db()
        async with db.execute("SELECT 1") as cursor:
            await cursor.fetchone()
        return True
    except Exception:
        return False
