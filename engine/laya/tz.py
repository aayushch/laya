# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Timezone resolution that never raises.

The Windows-bundled CPython has no system IANA tz database. We ship the
`tzdata` package to provide one, but an already-installed runtime that
predates that dependency (or a stripped environment) can still lack it.
In that case even `ZoneInfo("UTC")` raises ZoneInfoNotFoundError, which
previously 500'd the budget endpoint and crashed the scheduler loop.

`safe_zoneinfo` degrades gracefully: requested zone -> UTC via tzdata ->
`datetime.timezone.utc`, which is built into the stdlib and needs no data
files. Always returns a usable tzinfo.
"""

from __future__ import annotations

from datetime import timezone, tzinfo

import structlog

log = structlog.get_logger()

_warned = False


def safe_zoneinfo(name: str = "UTC") -> tzinfo:
    """Return a tzinfo for ``name``, falling back to UTC if it can't be loaded."""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:
            global _warned
            if not _warned:
                # tzdata is missing entirely — warn once, then use the
                # stdlib UTC constant which requires no data files.
                _warned = True
                log.warning("tzdata_unavailable_falling_back_to_utc", requested=name)
            return timezone.utc
