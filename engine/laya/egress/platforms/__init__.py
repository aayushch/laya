# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Per-platform egress adapters.

Each ``platforms/<name>.py`` defines one ``Platform`` subclass (see ``base.py``)
and exposes it as a module-level singleton ``PLATFORM`` (calendar exposes two
leaves). Each adapter is the single source of truth for everything about its
platform: behavior + capabilities + terminal events + compose/draft/source-ref
data. The registry facade (``laya.egress.registry``) imports these adapters and
delegates to them.

Two maps:
- ``registry_platforms()`` — the canonical platform→adapter map (all platforms,
  incl. smtp), in the historical capability order. The registry facade iterates
  this for capability lookups and derived groupings.
- ``for_platform(name)`` — enrichment dispatch. Excludes smtp (its egress goes
  through SmtpBackend, not n8n enrichment), and resolves the three calendar keys.

To add a platform: implement the ``Platform`` interface in a new file, expose
``PLATFORM``, and register it in ``_REGISTRY`` (and ``_DISPATCH`` if it enriches).
"""

from laya.egress.platforms.base import Platform
from laya.egress.platforms import (
    bitbucket,
    calendar,
    github,
    gmail,
    jira,
    linear,
    notion,
    outlook,
    slack,
    smtp,
)

# Canonical platform -> adapter. Order matches the historical _CAPABILITIES
# insertion order so get_all_platforms() / compose ordering stay stable.
_REGISTRY: dict[str, Platform] = {
    "gmail": gmail.PLATFORM,
    "outlook": outlook.PLATFORM,
    "smtp": smtp.PLATFORM,
    "jira": jira.PLATFORM,
    "notion": notion.PLATFORM,
    "github": github.PLATFORM,
    "bitbucket": bitbucket.PLATFORM,
    "slack": slack.PLATFORM,
    "linear": linear.PLATFORM,
    "calendar": calendar.GOOGLE_CALENDAR,
    "outlook_calendar": calendar.OUTLOOK_CALENDAR,
}

# Enrichment dispatch: platform string -> adapter. smtp is intentionally absent
# (SMTP egress goes through SmtpBackend, not n8n enrichment), so
# ``for_platform("smtp")`` stays None. The three calendar keys
# (google_calendar / outlook_calendar / calendar) resolve to a working adapter.
_DISPATCH: dict[str, Platform] = {
    "github": github.PLATFORM,
    "jira": jira.PLATFORM,
    "linear": linear.PLATFORM,
    "notion": notion.PLATFORM,
    "bitbucket": bitbucket.PLATFORM,
    "gmail": gmail.PLATFORM,
    "outlook": outlook.PLATFORM,
    "slack": slack.PLATFORM,
    "google_calendar": calendar.GOOGLE_CALENDAR,
    "outlook_calendar": calendar.OUTLOOK_CALENDAR,
    "calendar": calendar.GOOGLE_CALENDAR,
}


def for_platform(name: str) -> Platform | None:
    """Return the adapter for enrichment dispatch, or ``None`` if unsupported."""
    return _DISPATCH.get(name)


def registry_platforms() -> dict[str, Platform]:
    """Canonical platform→adapter map (all platforms, ordered) for the registry facade."""
    return _REGISTRY


__all__ = [
    "Platform",
    "bitbucket",
    "calendar",
    "github",
    "gmail",
    "jira",
    "linear",
    "notion",
    "outlook",
    "slack",
    "smtp",
    "for_platform",
    "registry_platforms",
]
