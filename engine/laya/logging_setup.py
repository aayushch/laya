"""Structured logging configuration using structlog."""

import logging
import sys
from pathlib import Path

import structlog

from laya.config import LAYA_LOG_DIR, ensure_directories


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog with console and file output."""
    ensure_directories()

    log_file = LAYA_LOG_DIR / "engine.log"

    # Standard library logging for file output
    file_handler = logging.FileHandler(log_file)
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
