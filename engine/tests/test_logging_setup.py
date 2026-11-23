# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""The structlog processor chains that setup_logging installs.

The tests build loggers from the chains directly: setup_logging reconfigures
global logging and writes under HOME.
"""

import json

import structlog

from laya.logging_setup import _structlog_processors


def test_json_chain_renders_the_traceback():
    """The packaged app logs JSON; a warning logged with exc_info carries the
    formatted traceback, not a bare flag."""
    log = structlog.wrap_logger(structlog.ReturnLogger(), processors=_structlog_processors(False))
    try:
        raise KeyError("space_id")
    except KeyError:
        out = log.warning("repos_scope_failed", exc_info=True)

    exception = json.loads(out)["exception"]
    assert "Traceback" in exception
    assert "KeyError" in exception


def test_console_chain_has_no_format_exc_info():
    """ConsoleRenderer formats exceptions itself."""
    assert structlog.processors.format_exc_info not in _structlog_processors(True)
