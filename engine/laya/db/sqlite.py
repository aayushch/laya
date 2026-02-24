"""Async SQLite connection management via aiosqlite."""

import aiosqlite
import structlog

from laya.config import DB_PATH, ensure_directories

log = structlog.get_logger()

# Module-level connection reference
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Get the current database connection. Raises if not connected."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call connect() first.")
    return _db


async def connect() -> aiosqlite.Connection:
    """Open the SQLite connection and enable WAL mode."""
    global _db
    ensure_directories()
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
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
