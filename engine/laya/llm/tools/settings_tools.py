# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Settings-related tool implementations for Laya chat."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from laya.api.websocket import manager
from laya.config import load_settings, save_settings
from laya.db.sqlite import get_db

log = structlog.get_logger()

# Keys never exposed via get_settings — security / complexity / cost concerns.
_SENSITIVE_KEYS = {"models", "custom_providers", "n8n", "pipeline", "privacy"}

# Allowed section names and their mapping to settings keys.
_SECTION_MAP: dict[str, list[str]] = {
    "appearance": ["theme"],
    "retention": ["retention"],
    "briefing": ["briefing"],
    "notifications": ["notifications"],
    "feed_preferences": ["feed_preferences"],
    "smart_grouping": ["smart_grouping"],
    "group_summaries": ["group_summaries"],
}

_VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_HH_MM_RE = re.compile(r"^\d{2}:\d{2}$")


async def _write_settings_audit(
    tool: str,
    section: str,
    old_value: Any,
    new_value: Any,
) -> None:
    """Append a settings-change row to audit_log with source='chat'."""
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO audit_log
               (log_id, step, success, metadata)
               VALUES (?, ?, ?, ?)""",
            (
                f"audit_{uuid.uuid4().hex[:12]}",
                "settings",
                True,
                json.dumps(
                    {
                        "source": "chat",
                        "tool": tool,
                        "section": section,
                        "old": old_value,
                        "new": new_value,
                    }
                ),
            ),
        )
        await db.commit()
    except Exception as exc:
        log.warning("settings_audit_log_failed", error=str(exc))


def _build_safe_settings(settings: dict, section: str | None) -> dict:
    """Return a sanitised view of settings for the requested section (or all)."""
    if section is not None:
        keys = _SECTION_MAP.get(section)
        if keys is None:
            return {
                "error": (
                    f"Unknown section '{section}'. "
                    f"Valid sections: {sorted(_SECTION_MAP)}"
                )
            }
        result: dict[str, Any] = {"section": section}
        for k in keys:
            if k == "theme":
                result["theme"] = settings.get("theme", "dark")
            else:
                result[k] = settings.get(k)
        return result

    # All sections — skip sensitive keys
    all_sections: dict[str, Any] = {}
    for sec, keys in _SECTION_MAP.items():
        entry: dict[str, Any] = {}
        for k in keys:
            if k == "theme":
                entry["theme"] = settings.get("theme", "dark")
            else:
                entry[k] = settings.get(k)
        all_sections[sec] = entry
    return {"sections": all_sections}


# ---------------------------------------------------------------------------
# Read tool
# ---------------------------------------------------------------------------


async def get_settings(section: str | None = None) -> dict[str, Any]:
    """Return current settings for a requested section or all safe sections.

    Args:
        section: One of "appearance", "retention", "briefing", "notifications",
                 "feed_preferences", "agent", or null for all.

    Returns:
        Dict with current setting values. Sensitive keys (models, api keys,
        n8n, pipeline, privacy) are never included.
    """
    settings = load_settings()
    return _build_safe_settings(settings, section)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


async def update_theme(theme: str) -> dict[str, Any]:
    """Switch the UI theme between dark and light mode.

    Args:
        theme: "dark" or "light".

    Returns:
        Dict with old_value and new_value for confirmation.
    """
    if theme not in ("dark", "light"):
        return {"error": "theme must be 'dark' or 'light'"}

    settings = load_settings()
    old_value = settings.get("theme", "dark")
    settings["theme"] = theme
    save_settings(settings)

    # Broadcast so any open tab updates immediately without a page reload
    await manager.broadcast(
        {
            "type": "settings_changed",
            "payload": {"section": "appearance", "new_value": {"theme": theme}},
        }
    )

    await _write_settings_audit("update_theme", "appearance", old_value, theme)
    log.info("settings_updated_via_chat", tool="update_theme", old=old_value, new=theme)

    return {"section": "appearance", "old_value": old_value, "new_value": theme, "success": True}


async def update_retention(
    card_retention_days: int | None = None,
    chat_retention_days: int | None = None,
) -> dict[str, Any]:
    """Change how long cards and chat history are retained.

    Args:
        card_retention_days: Days to keep action cards (1-365).
        chat_retention_days: Days to keep chat messages (1-365).

    Returns:
        Dict with old and new retention values.
    """
    if card_retention_days is None and chat_retention_days is None:
        return {"error": "Provide at least one of card_retention_days or chat_retention_days"}

    for field, value in [
        ("card_retention_days", card_retention_days),
        ("chat_retention_days", chat_retention_days),
    ]:
        if value is not None and not (1 <= value <= 365):
            return {"error": f"{field} must be between 1 and 365 (got {value})"}

    settings = load_settings()
    old_value = dict(settings.get("retention", {}))

    retention = dict(settings.get("retention", {}))
    if card_retention_days is not None:
        retention["card_retention_days"] = card_retention_days
    if chat_retention_days is not None:
        retention["chat_retention_days"] = chat_retention_days
    settings["retention"] = retention
    save_settings(settings)

    await _write_settings_audit("update_retention", "retention", old_value, retention)
    log.info("settings_updated_via_chat", tool="update_retention", old=old_value, new=retention)

    return {"section": "retention", "old_value": old_value, "new_value": retention, "success": True}


async def update_briefing(
    enabled: bool | None = None,
    time: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Toggle the daily briefing or change its schedule.

    Args:
        enabled: Enable or disable the daily briefing.
        time: Delivery time in HH:MM format (24-hour).
        timezone: IANA timezone string (e.g. "America/New_York").

    Returns:
        Dict with old and new briefing settings.
    """
    if enabled is None and time is None and timezone is None:
        return {"error": "Provide at least one of enabled, time, or timezone"}

    if time is not None and not _HH_MM_RE.match(time):
        return {"error": f"time must be in HH:MM format (got '{time}')"}

    if timezone is not None:
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(timezone)
        except (zoneinfo.ZoneInfoNotFoundError, KeyError):
            return {
                "error": (
                    f"Unknown timezone '{timezone}'. "
                    "Use an IANA timezone name (e.g. 'America/New_York')"
                )
            }

    settings = load_settings()
    old_value = dict(settings.get("briefing", {}))

    briefing = dict(settings.get("briefing", {}))
    if enabled is not None:
        briefing["enabled"] = enabled
    if time is not None:
        briefing["time"] = time
    if timezone is not None:
        briefing["timezone"] = timezone
    settings["briefing"] = briefing
    save_settings(settings)

    await _write_settings_audit("update_briefing", "briefing", old_value, briefing)
    log.info("settings_updated_via_chat", tool="update_briefing", old=old_value, new=briefing)

    return {"section": "briefing", "old_value": old_value, "new_value": briefing, "success": True}


async def update_notifications(
    enabled: bool | None = None,
    min_priority: str | None = None,
) -> dict[str, Any]:
    """Toggle notifications or change the minimum priority threshold.

    Args:
        enabled: Enable or disable notifications.
        min_priority: Minimum priority to notify for:
                      "LOW", "MEDIUM", "HIGH", or "CRITICAL".

    Returns:
        Dict with old and new notification settings.
    """
    if enabled is None and min_priority is None:
        return {"error": "Provide at least one of enabled or min_priority"}

    if min_priority is not None and min_priority not in _VALID_PRIORITIES:
        return {
            "error": (
                f"min_priority must be one of {sorted(_VALID_PRIORITIES)} "
                f"(got '{min_priority}')"
            )
        }

    settings = load_settings()
    old_value = dict(settings.get("notifications", {}))

    notifications = dict(settings.get("notifications", {}))
    if enabled is not None:
        notifications["enabled"] = enabled
    if min_priority is not None:
        notifications["min_priority"] = min_priority
    settings["notifications"] = notifications
    save_settings(settings)

    await _write_settings_audit("update_notifications", "notifications", old_value, notifications)
    log.info(
        "settings_updated_via_chat",
        tool="update_notifications",
        old=old_value,
        new=notifications,
    )

    return {
        "section": "notifications",
        "old_value": old_value,
        "new_value": notifications,
        "success": True,
    }


async def update_feed_preferences(
    sortBy: str | None = None,
    showArchived: bool | None = None,
    showBookmarked: bool | None = None,
    showUnreadOnly: bool | None = None,
    statusFilters: list[str] | None = None,
    priorityFilters: list[str] | None = None,
    spaceFilter: str | None = None,
) -> dict[str, Any]:
    """Change default feed view settings.

    Args:
        sortBy: Sort order — "newest", "priority", "category", or "platform".
        showArchived: Whether to show archived cards in the feed.
        showBookmarked: Whether to show only bookmarked cards.
        showUnreadOnly: Whether to show only unread cards.
        statusFilters: List of statuses to filter by (empty list = show all).
        priorityFilters: List of priorities to filter by (empty list = show all).
        spaceFilter: Space ID to filter cards by, or null for all spaces.

    Returns:
        Dict with old and new feed preference values.
    """
    valid_sorts = {"newest", "priority", "category", "platform"}
    if sortBy is not None and sortBy not in valid_sorts:
        return {"error": f"sortBy must be one of {sorted(valid_sorts)} (got '{sortBy}')"}

    if statusFilters is not None and not isinstance(statusFilters, list):
        return {"error": "statusFilters must be a list of status strings"}

    if priorityFilters is not None and not isinstance(priorityFilters, list):
        return {"error": "priorityFilters must be a list of priority strings"}

    if (
        sortBy is None
        and showArchived is None
        and showBookmarked is None
        and showUnreadOnly is None
        and statusFilters is None
        and priorityFilters is None
        and spaceFilter is None
    ):
        return {"error": "Provide at least one feed preference to update"}

    settings = load_settings()
    old_value = dict(settings.get("feed_preferences", {}))

    prefs = dict(settings.get("feed_preferences", {}))
    if sortBy is not None:
        prefs["sortBy"] = sortBy
    if showArchived is not None:
        prefs["showArchived"] = showArchived
    if showBookmarked is not None:
        prefs["showBookmarked"] = showBookmarked
    if showUnreadOnly is not None:
        prefs["showUnreadOnly"] = showUnreadOnly
    if statusFilters is not None:
        prefs["statusFilters"] = statusFilters
    if priorityFilters is not None:
        prefs["priorityFilters"] = priorityFilters
    if spaceFilter is not None:
        prefs["spaceFilter"] = spaceFilter
    settings["feed_preferences"] = prefs
    save_settings(settings)

    await _write_settings_audit(
        "update_feed_preferences", "feed_preferences", old_value, prefs
    )
    log.info(
        "settings_updated_via_chat",
        tool="update_feed_preferences",
        old=old_value,
        new=prefs,
    )

    return {
        "section": "feed_preferences",
        "old_value": old_value,
        "new_value": prefs,
        "success": True,
    }


_VALID_STRICTNESS = {"strict", "balanced", "lenient"}


async def update_smart_grouping(
    context_association: bool | None = None,
    smart_display: bool | None = None,
    strictness: str | None = None,
) -> dict[str, Any]:
    """Toggle context-based grouping and its display in the feed.

    Context association detects related cards across platforms (e.g. a Jira
    ticket and a linked PR).  Smart display groups those related cards
    together visually in the feed.

    Args:
        context_association: Enable or disable context-based grouping of
                             related cards across platforms.
        smart_display: Show or hide context groups in the feed view.
                       Only takes effect when context_association is enabled.
        strictness: How aggressively to group cards —
                    "strict", "balanced", or "lenient".

    Returns:
        Dict with old and new smart_grouping values.
    """
    if context_association is None and smart_display is None and strictness is None:
        return {"error": "Provide at least one of context_association, smart_display, or strictness"}

    if strictness is not None and strictness not in _VALID_STRICTNESS:
        return {
            "error": (
                f"strictness must be one of {sorted(_VALID_STRICTNESS)} "
                f"(got '{strictness}')"
            )
        }

    settings = load_settings()
    old_value = dict(settings.get("smart_grouping", {}))

    grouping = dict(settings.get("smart_grouping", {}))
    if context_association is not None:
        grouping["context_association"] = context_association
        if not context_association:
            grouping["smart_display"] = False
    if smart_display is not None:
        grouping["smart_display"] = smart_display
    if strictness is not None:
        grouping["strictness"] = strictness
    settings["smart_grouping"] = grouping
    save_settings(settings)

    await manager.broadcast(
        {
            "type": "settings_changed",
            "payload": {"section": "smart_grouping", "new_value": grouping},
        }
    )

    await _write_settings_audit(
        "update_smart_grouping", "smart_grouping", old_value, grouping
    )
    log.info(
        "settings_updated_via_chat",
        tool="update_smart_grouping",
        old=old_value,
        new=grouping,
    )

    return {
        "section": "smart_grouping",
        "old_value": {
            "context_association": old_value.get("context_association"),
            "smart_display": old_value.get("smart_display"),
            "strictness": old_value.get("strictness"),
        },
        "new_value": {
            "context_association": grouping.get("context_association"),
            "smart_display": grouping.get("smart_display"),
            "strictness": grouping.get("strictness"),
        },
        "success": True,
    }
