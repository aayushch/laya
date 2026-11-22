# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Apply source-confirmed GitHub and Jira card completion."""

from datetime import datetime, timezone

from pydantic import ValidationError

from laya.config import load_settings
from laya.db.timeutil import db_ts
from laya.models.event import LayaEvent
from laya.pipeline.card_status import SET_BY_LAYA, SET_BY_USER, StatusDecision, close_ready_cards
from laya.pipeline.summarize import trigger_summary_status_update
from laya.tasks import create_task

_GITHUB_MERGE = "pull_request_merged"
_GITHUB_CLOSED = "pull_request_closed"
_JIRA_STATUS_EVENTS = ("issue_resolved", "issue_status_changed")


def source_card_completion_enabled(platform: str) -> bool:
    return platform in {"github", "jira"} and bool(
        load_settings().get("pipeline", {}).get("source_card_completion", False)
    )


def source_completion_reason(event: LayaEvent) -> str | None:
    if not event.subject.id:
        return None

    event_type = event.source.raw_event_type
    if event.source.platform == "github" and event.subject.type == "pull_request":
        if event_type == _GITHUB_MERGE:
            return "GitHub pull request merged."
        merged_at = event.content.metadata.get("merged_at")
        if event_type == _GITHUB_CLOSED and isinstance(merged_at, str):
            try:
                merge_time = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            except ValueError:
                return None
            if _utc_timestamp(merge_time) == _utc_timestamp(event.timestamp):
                return "GitHub pull request merged."

    if event.source.platform == "jira" and event.subject.type == "ticket":
        metadata = event.content.metadata
        if (
            event_type in _JIRA_STATUS_EVENTS
            and str(metadata.get("jira_new_status", "")).strip().casefold() == "done"
            and str(metadata.get("jira_status") or "Done").strip().casefold() == "done"
        ):
            return "Jira issue moved to Done."
    return None


def source_status_lock_key(event: LayaEvent) -> str:
    # Some GitHub comment events have a ticket subject type. They still share
    # the PR's repo/number, and must serialize against its completion.
    if event.source.platform == "github" and event.subject.id:
        return f"github:pull_request:{event.subject.id}"
    return event.entity_id


def _utc_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


async def complete_source_cards(db, event: LayaEvent, space_id: str | None, reason: str) -> None:
    """Close work from this source at or before the merge or transition."""
    cutoff = _utc_timestamp(event.timestamp)
    cutoff_second = db_ts(cutoff)
    # Keep the indexed bound coarse and read the original timestamp for ties
    # so a later event in the same second stays open.
    rows = await db.execute_fetchall(
        """SELECT c.card_id, c.header, e.timestamp,
                  CASE WHEN e.timestamp = ? THEN e.raw_json END AS raw_json
           FROM events e
           JOIN action_cards c ON c.event_id = e.event_id
           WHERE e.subject_id = ? AND e.source_platform = ? AND e.source_connection_id IS ?
             AND COALESCE(c.space_id, 'default') = ? AND e.timestamp <= ?
             AND c.status = 'ready' AND c.status_set_by IS NOT ?""",
        (cutoff_second, event.subject.id, event.source.platform, event.source.connection_id,
         space_id or "default", cutoff_second, SET_BY_USER),
    )
    earlier_cards = []
    for row in rows:
        if row["timestamp"] == cutoff_second:
            try:
                earlier = LayaEvent.model_validate_json(row["raw_json"])
            except ValidationError:
                continue
            if _utc_timestamp(earlier.timestamp) > cutoff:
                continue
        earlier_cards.append(row)
    closed_cards = await close_ready_cards(earlier_cards, reason)
    for closed in closed_cards:
        create_task(
            trigger_summary_status_update(closed["card_id"], closed["header"], "done"),
            name=f"summary_status_{closed['card_id']}",
        )


async def apply_late_source_completion(
    db, event: LayaEvent, space_id: str | None, decision: StatusDecision,
) -> None:
    """Settle a Ready card if a later source completion was already applied."""
    if decision.status != "ready" or decision.status_set_by == SET_BY_USER or not event.subject.id:
        return
    platform = event.source.platform
    if platform not in {"github", "jira"}:
        return
    event_types = (_GITHUB_CLOSED, _GITHUB_MERGE) if platform == "github" else _JIRA_STATUS_EVENTS
    placeholders = ", ".join("?" for _ in event_types)
    timestamp = _utc_timestamp(event.timestamp)
    rows = await db.execute_fetchall(
        f"""SELECT e.raw_json FROM events e
            WHERE e.subject_id = ? AND e.source_platform = ? AND e.source_connection_id IS ?
              AND COALESCE(e.space_id, 'default') = ? AND e.timestamp >= ?
              AND e.event_id != ? AND e.processing_status = 'completed'
              AND e.source_raw_event_type IN ({placeholders})
              AND NOT EXISTS (SELECT 1 FROM action_cards c WHERE c.event_id = e.event_id)
            ORDER BY e.timestamp""",
        (event.subject.id, platform, event.source.connection_id,
         space_id or "default", db_ts(timestamp), event.event_id, *event_types),
    )
    for row in rows:
        try:
            completion = LayaEvent.model_validate_json(row["raw_json"])
        except ValidationError:
            continue
        if _utc_timestamp(completion.timestamp) < timestamp:
            continue
        reason = source_completion_reason(completion)
        if reason:
            decision.status = "done"
            decision.status_reason = reason
            decision.status_set_by = SET_BY_LAYA
            return
