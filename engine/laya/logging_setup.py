# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Structured logging configuration using structlog."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from laya.config import LAYA_LOG_DIR, ensure_directories, load_settings

# 10 MB per log file, keep 5 rotated backups (~60 MB max disk usage)
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Valid log-level names accepted from the LAYA_LOG_LEVEL env var / settings.json.
# Anything else falls back to INFO rather than crashing startup (a bad value in a
# user-edited settings.json must never take the engine down).
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_DEFAULT_LEVEL = "INFO"

# Handler names so apply_log_level() can find and re-level them at runtime without
# relying on fragile isinstance checks (RotatingFileHandler subclasses StreamHandler).
_FILE_HANDLER_NAME = "laya_file"
_CONSOLE_HANDLER_NAME = "laya_console"


def _coerce_level(name: str | None) -> int:
    """Map a level name to its logging int, defaulting to INFO on anything unknown."""
    upper = (name or "").strip().upper()
    if upper not in _VALID_LEVELS:
        upper = _DEFAULT_LEVEL
    return getattr(logging, upper)


def resolve_log_level() -> str:
    """Resolve the desired log level: LAYA_LOG_LEVEL env var, then settings.json
    ``logging.level``, then INFO.

    The env var wins so a power user (or a support session) can crank verbosity for
    one run without touching settings. Reading settings here is cheap (mtime-cached)
    and cycle-free — config.py does not import this module.
    """
    env = os.environ.get("LAYA_LOG_LEVEL")
    if env and env.strip().upper() in _VALID_LEVELS:
        return env.strip().upper()
    try:
        level = load_settings().get("logging", {}).get("level")
        if level and str(level).strip().upper() in _VALID_LEVELS:
            return str(level).strip().upper()
    except Exception:
        pass
    return _DEFAULT_LEVEL


def setup_logging(log_level: str | None = None) -> None:
    """Configure structlog with console and rotating file output.

    ``log_level`` defaults to :func:`resolve_log_level` (env var → settings → INFO).
    """
    ensure_directories()

    level = _coerce_level(log_level if log_level is not None else resolve_log_level())

    log_file = LAYA_LOG_DIR / "engine.log"

    # Rotating file handler: auto-rotates when file exceeds LOG_MAX_BYTES
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.name = _FILE_HANDLER_NAME
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.name = _CONSOLE_HANDLER_NAME
    # Prod console dedup: in the packaged app the Rust sidecar redirects our stdout
    # into engine-stdout.log, so every record the file_handler already wrote to
    # engine.log would be written a SECOND time (a blocking write on the event loop)
    # if the console handler mirrored the full level. When stdout is not a tty
    # (packaged app) keep the console at WARNING+ so engine-stdout.log only carries
    # warnings/errors + uvicorn output, not a duplicate copy of engine.log. In a dev
    # terminal (tty) keep the console at the full level so `dev.sh` output is complete.
    console_handler.setLevel(level if sys.stdout.isatty() else max(level, logging.WARNING))

    logging.basicConfig(
        level=level,
        handlers=[file_handler, console_handler],
        format="%(message)s",
    )

    # structlog configuration
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def apply_log_level(level_name: str) -> None:
    """Re-apply a log level to the already-configured root logger + handlers at
    runtime, so a change made in Settings takes effect without an engine restart.

    Best-effort: never raises (a settings save must not fail because logging couldn't
    be reconfigured). Mirrors the same prod console-dedup rule as setup_logging().
    """
    try:
        level = _coerce_level(level_name)
        root = logging.getLogger()
        root.setLevel(level)
        for handler in root.handlers:
            if handler.name == _CONSOLE_HANDLER_NAME:
                handler.setLevel(level if sys.stdout.isatty() else max(level, logging.WARNING))
            else:
                handler.setLevel(level)
    except Exception:
        pass
