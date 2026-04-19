"""Per-platform helper modules for egress payload handling.

Each module exposes three functions used by ``laya.egress.backends.n8n``:

- ``identifiers_from_event(action_type, event_id, content_metadata, event_row,
  self_emails=None) -> dict`` — derive identifier fields deterministically
  from the source event. Empty dict when nothing can be derived (e.g. no
  source event).
- ``normalize_payload(action_type, payload) -> dict`` — coerce LLM field
  variants (``body`` → ``comment``, ``issueKey`` → ``issue_key``, URL refs,
  etc.) and apply platform defaults.
- ``validate_payload(action_type, payload) -> list[str]`` — return human
  readable errors for payloads that the executor would reject.

These modules contain **no HTTP/network code**. All egress goes through
``laya.egress.backends.n8n`` which POSTs to the n8n executor workflows.
"""

from laya.egress.platforms import (
    bitbucket,
    calendar,
    github,
    gmail,
    jira,
    linear,
    outlook,
    slack,
)

_MODULES = {
    "github": github,
    "jira": jira,
    "linear": linear,
    "bitbucket": bitbucket,
    "gmail": gmail,
    "outlook": outlook,
    "slack": slack,
    "google_calendar": calendar,
    "outlook_calendar": calendar,
    "calendar": calendar,
}


def for_platform(name: str):
    """Return the helper module for a platform, or ``None`` if unsupported."""
    return _MODULES.get(name)


__all__ = [
    "bitbucket",
    "calendar",
    "github",
    "gmail",
    "jira",
    "linear",
    "outlook",
    "slack",
    "for_platform",
]
