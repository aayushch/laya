# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Structured logging configuration using structlog."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from laya.config import LAYA_LOG_DIR, ensure_directories

# 10 MB per log file, keep 5 rotated backups (~60 MB max disk usage)
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog with console and rotating file output."""
    ensure_directories()

    log_file = LAYA_LOG_DIR / "engine.log"

    # Rotating file handler: auto-rotates when file exceeds LOG_MAX_BYTES
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, log_level))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level))

    logging.basicConfig(
        level=getattr(logging, log_level),
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
