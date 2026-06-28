# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Egress platform registry — a thin facade over the per-platform adapters.

Each platform's data (capabilities, terminal events, compose/draft/source-ref
config, body field, chapter default, polish guidance, …) lives in its own
``platforms/<name>.py`` as a ``Platform`` subclass. This module imports those
adapters (via ``platforms.registry_platforms()``) and its ``get_*`` functions
delegate to them — so adding a platform means writing one file, not editing a
dozen tables here.

What still lives here is the genuinely CROSS-CUTTING data that has no single
platform owner:
- ``_FIELD_META`` — compose-form metadata keyed by *field name* (shared across
  platforms).
- ``_COMPOSE_HIDDEN_FIELDS`` / ``_NON_COMPOSABLE_ACTIONS`` — global sets.
- ``_LEGACY_PLATFORM_MAP`` — keyed by pre-Spaces *connection_id*.
- ``_PLATFORM_KEYWORDS`` — workflow-name keywords (includes discord/gitlab, which
  have no adapter).
"""

from laya.egress import platforms as _platforms
from laya.egress.models import EgressCapability
from laya.egress.platforms.base import DEFAULT_DRAFT_SCHEMA


def _adapter(platform: str):
    """Return the ``Platform`` adapter for a key, or ``None``."""
    return _platforms.registry_platforms().get(platform)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def get_capabilities(platform: str) -> list[EgressCapability]:
    """Return the list of capabilities for a platform."""
    a = _adapter(platform)
    return list(a.capabilities) if a else []


def get_all_platforms() -> list[str]:
    """Return all registered platform identifiers."""
    return list(_platforms.registry_platforms().keys())


def supports_action(platform: str, action_type: str) -> bool:
    """Check if a platform supports a specific action type."""
    a = _adapter(platform)
    return bool(a and any(c.action_type == action_type for c in a.capabilities))


def get_capability(platform: str, action_type: str) -> EgressCapability | None:
    """Get a specific capability by platform and action type."""
    a = _adapter(platform)
    if not a:
        return None
    for cap in a.capabilities:
        if cap.action_type == action_type:
            return cap
    return None


# ---------------------------------------------------------------------------
# Compose / draft guidance
# ---------------------------------------------------------------------------


def get_draft_schema(platform: str) -> dict:
    """Return the structured output JSON schema for composing on a platform."""
    a = _adapter(platform)
    return a.draft_schema if a else DEFAULT_DRAFT_SCHEMA


def get_body_field(platform: str) -> str:
    """Return the name of the primary text/body field for a platform."""
    a = _adapter(platform)
    return a.body_field if a else "body"


def get_compose_guidance(platform: str) -> str:
    """Return LLM compose guidance for a platform, or empty string if unknown."""
    a = _adapter(platform)
    return a.compose_guidance if a else ""


def get_platform_hint(platform: str) -> str:
    """Return a short human-readable description like 'a professional email'."""
    a = _adapter(platform)
    return a.platform_hint if a else f"a {platform} message"


# ---------------------------------------------------------------------------
# Terminal inbound event types — delegated to each adapter's
# ``terminal_event_types`` (the single source of truth). When such an event
# arrives, the pipeline auto-resolves pending sibling cards in the same entity
# group (see emit.run_emit -> LayaEvent.is_terminal). Per-platform values are
# kept in lock-step with the n8n ingestion workflows by
# tests/test_terminal_event_parity.py.
# ---------------------------------------------------------------------------


def is_terminal_event(platform: str, raw_event_type: str) -> bool:
    """True when this platform event means the underlying work item has
    completed, so pending sibling cards in its entity group can be auto-resolved."""
    a = _adapter(platform)
    return bool(a and raw_event_type in a.terminal_event_types)


# Derived snapshot of per-platform terminal event types, built from the adapters.
# Kept importable for tests/test_terminal_event_parity.py; includes only the
# platforms that actually declare terminal events (github/bitbucket/jira/linear).
_TERMINAL_EVENT_TYPES: dict[str, frozenset[str]] = {
    name: a.terminal_event_types
    for name, a in _platforms.registry_platforms().items()
    if a.terminal_event_types
}


# ---------------------------------------------------------------------------
# Platform keywords — used to detect platform from n8n workflow names.
# Cross-cutting: includes discord/gitlab (no adapter) and omits smtp/
# outlook_calendar, so it cannot be derived from the adapters.
# ---------------------------------------------------------------------------

_PLATFORM_KEYWORDS: dict[str, str] = {
    "github": "github",
    "gmail": "gmail",
    "jira": "jira",
    "slack": "slack",
    "bitbucket": "bitbucket",
    "calendar": "calendar",
    "outlook": "outlook",
    "linear": "linear",
    "notion": "notion",
    "discord": "discord",
    "gitlab": "gitlab",
}


def get_platform_keywords() -> dict[str, str]:
    """Return keyword→platform mapping for workflow name parsing."""
    return dict(_PLATFORM_KEYWORDS)


# ---------------------------------------------------------------------------
# Chapter defaults & polish guidance — delegated to the adapters.
# ---------------------------------------------------------------------------


def get_chapter_default(platform: str) -> str:
    """Return the default trace chapter label for a platform."""
    a = _adapter(platform)
    return a.chapter_default if a else "Update"


def get_polish_guidance(platform: str) -> str:
    """Return LLM polish tone guidance for a platform."""
    a = _adapter((platform or "").lower())
    return a.polish_guidance if a else "General professional correspondence."


# ---------------------------------------------------------------------------
# Legacy platform map — pre-Spaces hardcoded connection_id values. Cross-cutting
# (keyed by connection_id, not platform), so it stays here.
# ---------------------------------------------------------------------------

_LEGACY_PLATFORM_MAP: dict[str, str] = {
    "gmail_main": "gmail",
    "jira_main": "jira",
    "slack_main": "slack",
    "calendar_main": "calendar",
    "bb_main": "bitbucket",
    "outlook_main": "outlook",
    "outlook_calendar_main": "outlook_calendar",
}


def resolve_legacy_platform(connection_id: str) -> str | None:
    """Map a legacy hardcoded connection_id to its platform, or None."""
    return _LEGACY_PLATFORM_MAP.get(connection_id)


# ---------------------------------------------------------------------------
# Source reference formatting — how to display subject IDs per platform. The
# per-platform config lives on each adapter (``source_ref_config``); the
# formatting logic stays here.
# ---------------------------------------------------------------------------


def format_source_ref(
    platform: str,
    subject_id: str,
    subject_type: str | None,
    subject_title: str | None,
    source_url: str | None,
) -> tuple[str | None, str | None]:
    """Format a source reference and URL for display.

    Returns (source_ref, source_url) — either may be None.
    """
    a = _adapter(platform)
    config = a.source_ref_config if a else {}

    # Repo-qualified subject IDs (e.g. "owner/repo/#42", "ws/repo/PR-69")
    # need the repo prefix stripped before template substitution.
    display_id = subject_id
    repo_prefix = ""
    if platform in ("github", "bitbucket") and "/" in subject_id:
        parts = subject_id.rsplit("/", 1)
        repo_prefix = parts[0]
        display_id = parts[1]

    if config.get("use_title"):
        ref = subject_title or subject_id
    elif subject_type == "pull_request" and "pr_format" in config:
        ref = config["pr_format"].replace("{id}", display_id)
    elif "default_format" in config:
        ref = config["default_format"].replace("{id}", display_id)
    else:
        ref = subject_id or None

    if repo_prefix and ref:
        ref = f"{repo_prefix} {ref}"

    if not source_url and "url_template" in config and subject_id:
        source_url = config["url_template"].replace("{id}", subject_id)

    return ref, source_url


# ---------------------------------------------------------------------------
# Platform groups — derive from capabilities for tool enum lists
# ---------------------------------------------------------------------------


def get_email_platforms() -> list[str]:
    """Platforms that support send_email action."""
    result = [
        name for name, a in _platforms.registry_platforms().items()
        if any(c.action_type == "send_email" for c in a.capabilities)
    ]
    if "smtp" not in result:
        result.append("smtp")
    return result


def get_ticket_platforms() -> list[str]:
    """Platforms that support comment or create_issue actions."""
    return [
        name for name, a in _platforms.registry_platforms().items()
        if any(c.action_type in ("comment", "create_issue") for c in a.capabilities)
    ]


def get_transition_platforms() -> list[str]:
    """Platforms that support status transitions."""
    return [
        name for name, a in _platforms.registry_platforms().items()
        if any(c.action_type in ("transition", "update_status") for c in a.capabilities)
    ]


def get_pr_platforms() -> list[str]:
    """Platforms that support PR actions (approve, merge, etc.)."""
    return [
        name for name, a in _platforms.registry_platforms().items()
        if any(c.action_type in ("approve_pr", "merge_pr") for c in a.capabilities)
    ]


def get_composable_platforms() -> list[str]:
    """Platforms that can be used with the open_compose tool."""
    return list(_platforms.registry_platforms().keys())


def get_composable_actions(platform: str) -> list[EgressCapability]:
    """Return all composable actions for *platform* (non-composable filtered out)."""
    a = _adapter(platform)
    caps = a.capabilities if a else []
    return [c for c in caps if c.action_type not in _NON_COMPOSABLE_ACTIONS]


# ---------------------------------------------------------------------------
# Compose UI — field metadata for dynamic form rendering. Cross-cutting:
# _FIELD_META is keyed by field NAME (shared across platforms), and the hidden/
# non-composable sets are global.
# ---------------------------------------------------------------------------

_FIELD_META: dict[str, dict] = {
    # Email — scope "all" because recipients can be anyone
    "to": {"label": "To", "type": "email", "placeholder": "recipient@example.com", "autocomplete": {"scope": "all", "sources": ["email"]}},
    "cc": {"label": "CC", "type": "email", "placeholder": "cc@example.com", "autocomplete": {"scope": "all", "sources": ["email"]}},
    "bcc": {"label": "BCC", "type": "email", "placeholder": "bcc@example.com", "autocomplete": {"scope": "all", "sources": ["email"]}},
    "subject": {"label": "Subject", "type": "text", "placeholder": "Subject"},
    "body": {"label": "Body", "type": "textarea", "placeholder": "Write your message..."},
    # Slack
    "channel": {"label": "Channel", "type": "text", "placeholder": "#general"},
    "message": {"label": "Message", "type": "textarea", "placeholder": "Write your message..."},
    "thread_ts": {"label": "Thread Timestamp", "type": "text", "placeholder": "1234567890.123456"},
    # Jira
    "issue_key": {"label": "Issue Key", "type": "text", "placeholder": "PROJ-123"},
    "project": {"label": "Project", "type": "text", "placeholder": "PROJ"},
    "summary": {"label": "Summary", "type": "text", "placeholder": "Issue summary"},
    "priority": {"label": "Priority", "type": "select", "options": ["Highest", "High", "Medium", "Low", "Lowest"]},
    "type": {"label": "Type", "type": "select", "options": ["Bug", "Task", "Story"]},
    # GitHub / Bitbucket
    "repo": {"label": "Repository", "type": "text", "placeholder": "owner/repo"},
    "issue_number": {"label": "Issue #", "type": "text", "placeholder": "123"},
    "pr_number": {"label": "PR #", "type": "text", "placeholder": "456"},
    "pr_id": {"label": "PR ID", "type": "text", "placeholder": "123"},
    # Linear
    "issue_id": {"label": "Issue ID", "type": "text", "placeholder": "Issue ID"},
    "team_id": {"label": "Team", "type": "text", "placeholder": "Team key or ID"},
    "state_id": {"label": "State", "type": "text", "placeholder": "State ID"},
    "assignee_id": {"label": "Assignee", "type": "text", "placeholder": "User ID", "autocomplete": {"scope": "platform", "sources": ["email", "username"]}},
    # Notion
    "page_id": {"label": "Page ID", "type": "text", "placeholder": "Notion page ID"},
    "parent_id": {"label": "Parent ID", "type": "text", "placeholder": "Notion parent page/database ID"},
    "property_name": {"label": "Property", "type": "text", "placeholder": "Property name"},
    "property_value": {"label": "Value", "type": "text", "placeholder": "New value"},
    "text": {"label": "Text", "type": "textarea", "placeholder": "Write your text..."},
    # URL
    "url": {"label": "URL", "type": "url", "placeholder": "https://..."},
    # Shared
    "title": {"label": "Title", "type": "text", "placeholder": "Title"},
    "description": {"label": "Description", "type": "textarea", "placeholder": "Describe..."},
    "comment": {"label": "Comment", "type": "textarea", "placeholder": "Write your comment..."},
    "target_status": {"label": "Target Status", "type": "text", "placeholder": "Done"},
    "labels": {"label": "Labels", "type": "text", "placeholder": "bug, enhancement (comma-separated)"},
    "assignee": {"label": "Assignee", "type": "text", "placeholder": "Username or email", "autocomplete": {"scope": "platform", "sources": ["email", "username"]}},
    "assignees": {"label": "Assignees", "type": "text", "placeholder": "Usernames (comma-separated)", "autocomplete": {"scope": "platform", "sources": ["email", "username"]}},
    "emoji": {"label": "Emoji", "type": "text", "placeholder": ":thumbsup:"},
    "merge_method": {"label": "Merge Method", "type": "select", "options": ["squash", "merge", "rebase"]},
    "commit_title": {"label": "Commit Title", "type": "text", "placeholder": "Merge commit title"},
    # Calendar — scope "all" because attendees can be anyone
    "start": {"label": "Start", "type": "datetime-local", "placeholder": ""},
    "end": {"label": "End", "type": "datetime-local", "placeholder": ""},
    "location": {"label": "Location", "type": "text", "placeholder": "Room / URL"},
    "attendees": {"label": "Attendees", "type": "text", "placeholder": "email@example.com (comma-separated)", "autocomplete": {"scope": "all", "sources": ["email"]}},
}

# Fields that are never shown in the composer — engine-internal IDs or
# fields derived from another visible field (e.g. owner from repo).
_COMPOSE_HIDDEN_FIELDS = {
    "gmail_id",             # internal Gmail message ID
    "outlook_id",           # internal Outlook message ID
    "timestamp",            # Slack reaction timestamp
    "owner",                # derived from repo field (owner/repo format)
    "workspace",            # derived from repo field (workspace/repo format)
    "parent_type",          # Notion internal
    "children",             # Notion internal (JSON block array)
    "properties",           # Notion internal (JSON properties)
    "block_type",           # Notion internal
    "close_source_branch",  # Bitbucket merge option
    "conversation_id",      # Outlook thread ID
    "merge_strategy",       # Bitbucket merge variant (use merge_method)
    "visibility",           # Jira comment visibility (rarely used manually)
}

_NON_COMPOSABLE_ACTIONS = {
    "archive", "star", "mark_read", "react",
    "archive_page",
    "forward",      # needs original email ID from event context
    "decline_pr",   # destructive — not a compose action
    "open_url",     # client-side only — no compose form needed
}


def get_compose_fields(platform: str, action_type: str) -> list[dict]:
    """Return the ordered field list for a compose action.

    Shows ALL required and optional fields except engine-internal ones
    listed in _COMPOSE_HIDDEN_FIELDS. This ensures the composer has
    every field the user needs to fill for manual composition.
    """
    cap = get_capability(platform, action_type)
    if not cap:
        return []

    seen: set[str] = set()
    fields: list[dict] = []

    def _add(name: str, required: bool) -> None:
        if name in seen or name in _COMPOSE_HIDDEN_FIELDS:
            return
        seen.add(name)
        meta = _FIELD_META.get(name, {
            "label": name.replace("_", " ").title(),
            "type": "text",
            "placeholder": "",
        })
        entry: dict = {
            "name": name,
            "required": required,
            "type": meta["type"],
            "label": meta["label"],
            "placeholder": meta.get("placeholder", ""),
        }
        if "options" in meta:
            entry["options"] = meta["options"]
        if "autocomplete" in meta:
            entry["autocomplete"] = meta["autocomplete"]
        fields.append(entry)

    for f in cap.requires_fields:
        _add(f, True)

    for f in cap.optional_fields:
        _add(f, False)

    # Group address fields (To, CC, BCC) together at the top, then text
    # inputs, then textareas last — so email recipients aren't buried
    # below the body.
    _TYPE_ORDER = {"email": 0, "text": 1, "datetime-local": 1, "select": 2, "textarea": 3}
    fields.sort(key=lambda f: _TYPE_ORDER.get(f["type"], 1))

    return fields


def get_compose_platforms_data() -> list[dict]:
    """Assemble the full compose-platforms response for the UI.

    Returns a list of platform dicts, each with id, label, icon, and actions.
    """
    from laya.integrations.platforms import PLATFORMS

    result: list[dict] = []

    for platform_id, adapter in _platforms.registry_platforms().items():
        composable_actions = [
            c for c in adapter.capabilities if c.action_type not in _NON_COMPOSABLE_ACTIONS
        ]
        if not composable_actions:
            continue

        meta = PLATFORMS.get(platform_id, {})
        label = meta.get("label", platform_id.title())
        icon = meta.get("icon", platform_id)

        actions = []
        for cap in composable_actions:
            actions.append({
                "action_type": cap.action_type,
                "label": cap.label,
                "fields": get_compose_fields(platform_id, cap.action_type),
            })

        result.append({
            "id": platform_id,
            "label": label,
            "icon": icon,
            "actions": actions,
        })

    return result
