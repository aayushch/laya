# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Omni API — rolling cross-platform summary endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.config import load_settings
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now, db_ts
from laya.models.card_lifecycle import TERMINAL_STATUSES
from laya.pipeline.omni_change import (
    SECTION_CHAIN,
    decorate_item_keys,
    merge_change_summaries,
    parse_change_summary,
)

log = structlog.get_logger()
router = APIRouter()

# How far back an item's lineage walk goes. Deep enough to cover a day of
# incremental writes plus the resynthesis that opened it, shallow enough that the
# walk stays one base reconstruction plus a delta replay (see _snapshot_states).
_LINEAGE_MAX_VERSIONS = 30


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class PinRequest(BaseModel):
    space_id: str = "default"
    text: str
    source_cards: list[str] = []
    platforms: list[str] = []


class BookmarkRequest(BaseModel):
    space_id: str = "default"
    source_card_id: str  # first source_card ID — unique identifier for the item
    bookmarked: bool = True


# ---------------------------------------------------------------------------
# Live decoration — item state as it is NOW, not as it was at synthesis
# ---------------------------------------------------------------------------


def _all_item_card_ids(sections: list[dict]) -> list[str]:
    ids: list[str] = []
    for section in sections or []:
        for item in section.get("items", []) or []:
            ids.extend(c for c in (item.get("source_cards") or []) if c)
    return ids


def _decorate_live(sections: list[dict], meta: dict[str, dict]) -> list[dict]:
    """Attach a `live` block to every item, in place.

    Everything the board's instruments and triage ordering need but that the
    stored snapshot cannot answer: `item.priority` is frozen at synthesis time,
    so a CRITICAL that has since been merged still reads CRITICAL. The pipeline
    already computed this for the LLM prompt and threw it away (handoff §B1).
    """
    from laya.pipeline.omni import _all_resolved, _live_max_priority

    for section in sections or []:
        for item in section.get("items", []) or []:
            cards = [c for c in (item.get("source_cards") or []) if c]
            known = [meta[c] for c in cards if c in meta]
            platform_counts: dict[str, int] = {}
            for m in known:
                p = (m.get("source_platform") or "unknown").lower()
                platform_counts[p] = platform_counts.get(p, 0) + 1
            created = sorted(m["created_at"] for m in known if m.get("created_at"))
            item["live"] = {
                "max_priority": _live_max_priority(cards, meta),
                "all_resolved": _all_resolved(cards, meta),
                "resolved_count": sum(
                    1 for m in known if m.get("status") in TERMINAL_STATUSES
                ),
                "missing_count": len(cards) - len(known),
                "oldest_created_at": created[0] if created else None,
                "platform_counts": platform_counts,
            }
    return sections


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@router.get("/omni")
async def get_omni(space_id: str = "default", version: int | None = None):
    """Get the latest (or specific version) Omni snapshot.

    Handles delta reconstruction transparently — callers always receive
    the full snapshot regardless of how it's stored.

    Items are decorated with a stable ``item_key`` and a ``live`` block (see
    ``_decorate_live``). Both are computed on read, so snapshots written before
    migration 072 gain them without a backfill.
    """
    from laya.pipeline.omni import _fetch_card_meta, _load_full_snapshot

    db = await get_db()

    # Reconstruct full state (handles delta chains automatically)
    content, ver, card_ids, meta = await _load_full_snapshot(db, space_id, version)

    if content is None:
        return {
            "snapshot_id": None,
            "space_id": space_id,
            "version": 0,
            "generated_at": None,
            "snapshot_type": None,
            "sections": [],
            "stats": {"events_processed": 0, "cards_acted_on": 0, "compression_ratio": 0.0},
            "card_ids": [],
            "change_summary": None,
        }

    sections = content.get("sections", [])
    decorate_item_keys(sections)
    card_meta = await _fetch_card_meta(db, _all_item_card_ids(sections))
    _decorate_live(sections, card_meta)

    change_rows = await db.execute_fetchall(
        "SELECT change_summary_json FROM omni_snapshots WHERE space_id = ? AND version = ?",
        (space_id, ver),
    )
    change_summary = (
        parse_change_summary(change_rows[0]["change_summary_json"]) if change_rows else None
    )

    return {
        "snapshot_id": meta.get("snapshot_id"),
        "space_id": space_id,
        "version": ver,
        "generated_at": meta.get("generated_at"),
        "snapshot_type": meta.get("snapshot_type"),
        "sections": sections,
        "stats": content.get("stats", {}),
        "card_ids": card_ids,
        "change_summary": change_summary,
    }


@router.get("/omni/changes")
async def get_omni_changes(
    space_id: str = "default",
    base: int | None = None,
    to: int | None = None,
):
    """What changed between two snapshot versions — the changelog rail's data.

    ``base`` is exclusive and ``to`` inclusive, so comparing v1219 → v1227 reads
    the summaries written by v1220..v1227 and merges them (an item added at v1220
    and resolved at v1226 reports once, as resolved — see merge_change_summaries).

    Versions whose ``change_summary_json`` is NULL predate migration 072; they are
    reported in ``unsummarized_versions`` so the UI can say "partial" rather than
    silently under-reporting.
    """
    db = await get_db()

    if to is None:
        latest = await db.execute_fetchall(
            "SELECT COALESCE(MAX(version), 0) AS mv FROM omni_snapshots WHERE space_id = ?",
            (space_id,),
        )
        to = latest[0]["mv"] if latest else 0

    if base is None:
        # Default comparison base: the newest version strictly older than `to`.
        prev = await db.execute_fetchall(
            """SELECT version FROM omni_snapshots
               WHERE space_id = ? AND version < ? ORDER BY version DESC LIMIT 1""",
            (space_id, to),
        )
        base = prev[0]["version"] if prev else max(to - 1, 0)

    rows = await db.execute_fetchall(
        """SELECT version, generated_at, snapshot_type, change_summary_json
           FROM omni_snapshots
           WHERE space_id = ? AND version > ? AND version <= ?
           ORDER BY version ASC""",
        (space_id, base, to),
    )

    summaries: list[dict] = []
    unsummarized: list[int] = []
    for row in rows:
        parsed = parse_change_summary(row["change_summary_json"])
        if parsed is None:
            unsummarized.append(row["version"])
        else:
            summaries.append(parsed)

    merged = merge_change_summaries(summaries)

    base_rows = await db.execute_fetchall(
        "SELECT generated_at, snapshot_type FROM omni_snapshots WHERE space_id = ? AND version = ?",
        (space_id, base),
    )

    return {
        "space_id": space_id,
        "base_version": base,
        "base_generated_at": base_rows[0]["generated_at"] if base_rows else None,
        "base_snapshot_type": base_rows[0]["snapshot_type"] if base_rows else None,
        "to_version": to,
        "versions_compared": len(rows),
        "unsummarized_versions": unsummarized,
        **merged,
    }


@router.get("/omni/history")
async def get_omni_history(space_id: str = "default", limit: int = 30):
    """List snapshot versions for time-slider navigation."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ?
           ORDER BY version DESC
           LIMIT ?""",
        (space_id, limit),
    )

    return {
        "space_id": space_id,
        "snapshots": [
            {
                "snapshot_id": row["snapshot_id"],
                "version": row["version"],
                "generated_at": row["generated_at"],
                "snapshot_type": row["snapshot_type"],
                "events_processed": row["events_processed"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Timeline (logarithmic sampling across retention window)
# ---------------------------------------------------------------------------


@router.get("/omni/timeline")
async def get_omni_timeline(space_id: str = "default"):
    """Return a three-tier sampled timeline for the Omni time-travel UI.

    Tiers:
      - today (past 24h): every snapshot
      - this_week (1-7 days ago): latest snapshot per hour
      - earlier (7+ days ago): only synthesis snapshots
    """
    db = await get_db()
    now = datetime.now(timezone.utc)
    today_start = db_ts(now - timedelta(hours=24))
    week_start = db_ts(now - timedelta(days=7))
    now_iso = db_ts(now)

    def _row_to_entry(row):
        return {
            "snapshot_id": row["snapshot_id"],
            "version": row["version"],
            "generated_at": row["generated_at"],
            "snapshot_type": row["snapshot_type"],
            "events_processed": row["events_processed"],
        }

    # Tier 1: Today — all snapshots
    today_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ? AND generated_at >= ?
           ORDER BY generated_at ASC""",
        (space_id, today_start),
    )

    # Tier 2: This week — latest snapshot per hour bucket
    week_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE snapshot_id IN (
               SELECT snapshot_id FROM (
                   SELECT snapshot_id,
                          ROW_NUMBER() OVER (
                              PARTITION BY strftime('%Y-%m-%d %H', generated_at)
                              ORDER BY version DESC
                          ) AS rn
                   FROM omni_snapshots
                   WHERE space_id = ? AND generated_at >= ? AND generated_at < ?
               ) WHERE rn = 1
           )
           ORDER BY generated_at ASC""",
        (space_id, week_start, today_start),
    )

    # Tier 3: Earlier — only synthesis snapshots
    earlier_rows = await db.execute_fetchall(
        """SELECT snapshot_id, version, generated_at, snapshot_type, events_processed
           FROM omni_snapshots
           WHERE space_id = ? AND generated_at < ?
             AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY generated_at ASC""",
        (space_id, week_start),
    )

    return {
        "space_id": space_id,
        "segments": [
            {
                "tier": "earlier",
                "label": "Earlier",
                "range_start": None,
                "range_end": week_start,
                "entries": [_row_to_entry(r) for r in earlier_rows],
            },
            {
                "tier": "this_week",
                "label": "This Week",
                "range_start": week_start,
                "range_end": today_start,
                "entries": [_row_to_entry(r) for r in week_rows],
            },
            {
                "tier": "today",
                "label": "Recent",
                "range_start": today_start,
                "range_end": now_iso,
                "entries": [_row_to_entry(r) for r in today_rows],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Resynthesis
# ---------------------------------------------------------------------------


@router.post("/omni/resynthesis")
async def trigger_resynthesis(space_id: str = "default"):
    """Manually trigger a full Omni resynthesis (runs in background).

    Returns immediately with 202 Accepted. The client should listen for
    the ``omni_updated`` WebSocket event to know when resynthesis is done.
    """
    import asyncio
    from laya.pipeline.omni import _get_gate, run_omni_resynthesis

    # The resynthesis gate doubles as a concurrency guard: if the gate is
    # cleared (not set), a resynthesis is already running for this space.
    gate = _get_gate(space_id)
    if not gate.is_set():
        raise HTTPException(
            status_code=409,
            detail="Resynthesis already in progress for this space",
        )

    async def _run():
        try:
            await run_omni_resynthesis(space_id=space_id, snapshot_type="manual")
        except Exception as e:
            log.error("omni_resynthesis_api_failed", space_id=space_id, error=str(e))

    asyncio.create_task(_run())

    from starlette.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "space_id": space_id},
    )


def _safe_zone(name: str | None):
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, ZoneInfoNotFoundError):
        return timezone.utc


def _rolling_anchor(last_synth_at: str | None) -> datetime | None:
    """The instant the rolling interval is measured from.

    The scheduler measures it from its own in-process ``_last_omni_rolling``,
    which is seeded to "now" on the first tick after startup — NOT from the last
    stored snapshot. Predicting from the snapshot instead would report a time
    long past on any instance that has been restarted since its last synthesis
    (observed on a 19-day-idle instance: "imminent", forever). Read the
    scheduler's anchor and fall back to the snapshot only when it hasn't ticked.
    """
    try:
        from laya.scheduler import omni_rolling_anchor

        anchor = omni_rolling_anchor()
        if anchor is not None:
            return anchor
    except Exception:  # pragma: no cover - scheduler not importable (CLI/tests)
        pass

    if not last_synth_at:
        return None
    try:
        last = datetime.fromisoformat(last_synth_at.replace("Z", "+00:00"))
        return last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _next_synthesis_at(omni_cfg: dict, last_synth_at: str | None) -> datetime | None:
    """When the scheduler will next resynthesize — earliest of EOD and rolling.

    Mirrors the two time-based triggers in `scheduler.py`. The event-threshold
    trigger has no clock time, so it can't be predicted here; the status endpoint
    reports `events_since_last` / `event_threshold` separately for that.
    """
    if not omni_cfg.get("enabled", False):
        return None

    tz = _safe_zone(omni_cfg.get("timezone"))
    now_local = datetime.now(tz)
    candidates: list[datetime] = []

    target = omni_cfg.get("resynthesis_time")
    if target:
        try:
            hh, mm = (int(p) for p in str(target).split(":"))
            eod = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if eod <= now_local:
                eod += timedelta(days=1)
            candidates.append(eod.astimezone(timezone.utc))
        except (TypeError, ValueError):
            pass

    try:
        rolling_hours = int(omni_cfg.get("rolling_interval_hours", 0) or 0)
    except (TypeError, ValueError):
        rolling_hours = 0
    if rolling_hours > 0:
        anchor = _rolling_anchor(last_synth_at)
        if anchor is not None:
            candidates.append(anchor + timedelta(hours=rolling_hours))

    if not candidates:
        return None
    return min(candidates)


@router.get("/omni/resynthesis/status")
async def resynthesis_status(space_id: str = "default"):
    """Whether a resynthesis is running, plus when the next one is due.

    The schedule fields let the compression instrument show "Next synthesis
    3h 38m" and the item page predict which synthesis will fold a line.
    """
    from laya.pipeline.omni import _get_gate

    db = await get_db()
    gate = _get_gate(space_id)
    omni_cfg = load_settings().get("omni", {})

    last_rows = await db.execute_fetchall(
        """SELECT generated_at FROM omni_snapshots
           WHERE space_id = ? AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )
    last_synth_at = last_rows[0]["generated_at"] if last_rows else None

    # Same query the scheduler's threshold trigger runs, so the number the UI
    # shows and the number that fires a resynthesis are the same number.
    since = last_synth_at or "2000-01-01 00:00:00"
    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM action_cards WHERE space_id = ? AND created_at > ?",
        (space_id, since),
    )
    events_since_last = count_rows[0]["cnt"] if count_rows else 0

    next_at = _next_synthesis_at(omni_cfg, last_synth_at)

    try:
        interval_hours = int(omni_cfg.get("rolling_interval_hours", 0) or 0)
    except (TypeError, ValueError):
        interval_hours = 0
    try:
        event_threshold = int(omni_cfg.get("event_threshold", 50))
    except (TypeError, ValueError):
        event_threshold = 50

    return {
        "space_id": space_id,
        "in_progress": not gate.is_set(),
        "next_scheduled_at": db_ts(next_at) if next_at else None,
        "interval_hours": interval_hours,
        "event_threshold": event_threshold,
        "events_since_last": events_since_last,
        "last_synthesis_at": last_synth_at,
    }


# ---------------------------------------------------------------------------
# Event volume — the instrument cluster's 14-day bars and platform mix
# ---------------------------------------------------------------------------


@router.get("/omni/volume")
async def get_omni_volume(
    space_id: str = "default",
    days: int = 14,
    tz: str | None = None,
):
    """Per-day event counts and platform mix over a trailing window.

    Neither instrument can be served from the snapshot table: `events_processed`
    on a snapshot row is a cumulative card total, not a per-day count, and the
    platform mix has to count raw events (including filtered ones that never
    produced a card) or it understates every high-volume source.

    Bucketing is done in SQL after shifting stored UTC timestamps by the client's
    offset, matching `/events/day` — same DST caveat, same reason.
    """
    db = await get_db()
    days = max(1, min(90, days))

    local_tz = _safe_zone(tz)
    now_local = datetime.now(local_tz)
    start_local = (now_local - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    offset = start_local.utcoffset() or timedelta(0)
    offset_minutes = int(offset.total_seconds() // 60)
    shift = f"{offset_minutes:+d} minutes"
    start_utc = db_ts(start_local.astimezone(timezone.utc))

    params: list = [shift, start_utc]
    where = "timestamp >= ?"
    space_ids = [s.strip() for s in (space_id or "").split(",") if s.strip()]
    if space_ids:
        placeholders = ",".join("?" for _ in space_ids)
        where += f" AND space_id IN ({placeholders})"
        params.extend(space_ids)

    rows = await db.execute_fetchall(
        f"""SELECT DATE(datetime(timestamp, ?)) AS day,
                   LOWER(COALESCE(source_platform, 'unknown')) AS platform,
                   COUNT(*) AS count
            FROM events
            WHERE {where}
            GROUP BY day, platform""",
        params,
    )

    by_day: dict[str, int] = {}
    platforms: dict[str, int] = {}
    for r in rows:
        day = r["day"]
        if not day:
            continue
        by_day[day] = by_day.get(day, 0) + r["count"]
        platforms[r["platform"]] = platforms.get(r["platform"], 0) + r["count"]

    # Emit every day in the window, zeros included — the bar chart needs a fixed
    # number of slots or a quiet weekend silently narrows the axis.
    series = []
    for i in range(days):
        d = (start_local + timedelta(days=i)).strftime("%Y-%m-%d")
        series.append({"date": d, "count": by_day.get(d, 0)})

    today = now_local.strftime("%Y-%m-%d")
    return {
        "space_id": space_id,
        "days": days,
        "series": series,
        "total": sum(by_day.values()),
        "today": by_day.get(today, 0),
        "today_date": today,
        "platforms": dict(sorted(platforms.items(), key=lambda kv: -kv[1])),
    }


# ---------------------------------------------------------------------------
# Item drill-down — one call for the claim, its cards, and its lineage
# ---------------------------------------------------------------------------

# Bucketing rules (handoff §B5). Derived from live card state, never from parsing
# the LLM's sentence — the sentence is prose, the buckets are structure.
_AWAITING_STATUSES = {"ready", "pending", "awaiting_input", "requires_approval"}
_CHANGES_EVENT_HINTS = (
    "changes_requested", "pr_declined", "build_failed", "check_failed",
    "pipeline_failed", "review_rejected",
)


def _bucket_for(status: str | None, platform: str | None, raw_event_type: str | None) -> str:
    """Outcome bucket for one evidence card.

    Order matters: a failed/changes-requested card is called out even when its
    status would otherwise read as awaiting, because that's the one the user has
    to look at first.
    """
    from laya.egress.registry import is_terminal_event

    raw = (raw_event_type or "").lower()

    if status == "failed" or any(hint in raw for hint in _CHANGES_EVENT_HINTS):
        return "changes_requested"
    if status in TERMINAL_STATUSES or (
        platform and raw and is_terminal_event(platform, raw_event_type or "")
    ):
        return "resolved"
    if status in _AWAITING_STATUSES:
        return "awaiting_you"
    return "other"


async def _snapshot_states(
    db, space_id: str, upto_version: int, limit: int = _LINEAGE_MAX_VERSIONS
) -> list[dict]:
    """Reconstructed sections for each of the last `limit` versions up to `upto_version`.

    One base reconstruction plus a delta replay, rather than calling
    `_load_full_snapshot` per version (which would re-walk the whole chain each
    time — 30 versions of a 20-deep chain is 600 JSON parses for one page open).
    """
    import copy

    from laya.pipeline.omni import _apply_delta, _load_full_snapshot

    rows = await db.execute_fetchall(
        """SELECT version, generated_at, snapshot_type, content_json, is_delta
           FROM omni_snapshots
           WHERE space_id = ? AND version <= ?
           ORDER BY version DESC LIMIT ?""",
        (space_id, upto_version, limit),
    )
    if not rows:
        return []

    rows = list(reversed(rows))  # oldest first — deltas must replay in order

    content: dict
    if rows[0]["is_delta"]:
        # The window opens mid-chain; rebuild the state just before it so the
        # first delta has something to apply to.
        prior, _v, _c, _m = await _load_full_snapshot(db, space_id, rows[0]["version"] - 1)
        content = prior or {"sections": []}
    else:
        content = {"sections": []}

    states: list[dict] = []
    for row in rows:
        if row["is_delta"]:
            content = _apply_delta(content, json.loads(row["content_json"]))
        else:
            content = json.loads(row["content_json"])
        # Deep-copied because _apply_delta mutates `content` in place on the next
        # iteration — holding a reference would rewrite history behind us.
        states.append({
            "version": row["version"],
            "generated_at": row["generated_at"],
            "snapshot_type": row["snapshot_type"],
            "sections": copy.deepcopy(content.get("sections", [])),
        })
    return states


def _locate_item(sections: list[dict], item_key: str, section_type: str | None) -> tuple[str, dict] | None:
    """Find an item by key, preferring the named section."""
    decorate_item_keys(sections)
    fallback: tuple[str, dict] | None = None
    for section in sections or []:
        stype = section.get("type") or ""
        for item in section.get("items", []) or []:
            if item.get("item_key") != item_key:
                continue
            if section_type is None or stype == section_type:
                return stype, item
            if fallback is None:
                fallback = (stype, item)
    return fallback


async def _build_lineage(
    db, space_id: str, version: int, section_type: str, item: dict, omni_cfg: dict
) -> dict:
    """Walk this item backwards through versions, matching on entity overlap.

    Answers the context rail's "how did this line get here": when it first
    appeared, how many syntheses carried it, how often its text was rewritten,
    and which section it will fold into next.
    """
    from laya.pipeline.omni_change import subject_matches

    states = await _snapshot_states(db, space_id, version)

    history: list[dict] = []
    rewrite_count = 0
    last_text: str | None = None

    for state in states:
        match: tuple[str, dict] | None = None
        for section in state["sections"]:
            for candidate in section.get("items", []) or []:
                if subject_matches(item, candidate):
                    match = (section.get("type") or "", candidate)
                    break
            if match:
                break
        if not match:
            continue
        stype, matched = match
        text = matched.get("text", "")
        if last_text is not None and text != last_text:
            rewrite_count += 1
        last_text = text
        history.append({
            "version": state["version"],
            "generated_at": state["generated_at"],
            "snapshot_type": state["snapshot_type"],
            "section": stype,
            "source_count": len(matched.get("source_cards") or []),
        })

    rank = SECTION_CHAIN.index(section_type) if section_type in SECTION_CHAIN else -1
    next_section = (
        SECTION_CHAIN[rank + 1] if 0 <= rank < len(SECTION_CHAIN) - 1 else None
    )

    last_rows = await db.execute_fetchall(
        """SELECT generated_at FROM omni_snapshots
           WHERE space_id = ? AND snapshot_type IN ('scheduled', 'rolling', 'manual')
           ORDER BY version DESC LIMIT 1""",
        (space_id,),
    )
    next_at = _next_synthesis_at(
        omni_cfg, last_rows[0]["generated_at"] if last_rows else None
    )

    return {
        "first_version": history[0]["version"] if history else version,
        "first_seen_at": history[0]["generated_at"] if history else None,
        "versions_carried": len(history),
        "rewrite_count": rewrite_count,
        # True when the walk hit its cap — the item may be older than we can say,
        # so the UI can render "7+" instead of an understated exact number.
        "truncated": len(states) >= _LINEAGE_MAX_VERSIONS and bool(history) and history[0]["version"] == states[0]["version"],
        "section_history": history,
        "next_fold": (
            {"to_section": next_section, "expected_at": db_ts(next_at) if next_at else None}
            if next_section
            else None
        ),
    }


@router.get("/omni/item")
async def get_omni_item(
    space_id: str = "default",
    v: int | None = None,
    section: str | None = None,
    item: str = "",
    tz: str | None = None,
):
    """The claim, its evidence cards, its lineage and its share of the day.

    Replaces the drill-down's N+1 `GET /cards/{id}` fan-out. Crucially it returns
    ``missing_card_ids`` instead of quietly dropping unresolvable cards the way
    the old client-side `Promise.allSettled` did: an aggregate that says 14 and
    can only open 12 must say so.
    """
    from laya.api.cards_common import CARD_SELECT_COLUMNS, _row_to_card
    from laya.pipeline.omni import _fetch_card_meta, _load_full_snapshot

    if not item:
        raise HTTPException(status_code=400, detail="item (item_key) is required")

    db = await get_db()
    content, ver, _card_ids, meta = await _load_full_snapshot(db, space_id, v)
    if content is None:
        raise HTTPException(status_code=404, detail="No Omni snapshot for this space")

    sections = content.get("sections", [])
    located = _locate_item(sections, item, section)
    if located is None:
        raise HTTPException(
            status_code=404,
            detail=f"Item {item} not found in snapshot v{ver}",
        )
    found_section, found_item = located

    source_cards = [c for c in (found_item.get("source_cards") or []) if c]
    card_meta = await _fetch_card_meta(db, source_cards)
    _decorate_live([{"type": found_section, "items": [found_item]}], card_meta)

    cards: list[dict] = []
    found_ids: set[str] = set()
    if source_cards:
        placeholders = ",".join("?" for _ in source_cards)
        rows = await db.execute_fetchall(
            f"""SELECT {CARD_SELECT_COLUMNS}, e.source_raw_event_type, e.source_platform
                FROM action_cards c
                LEFT JOIN events e ON c.event_id = e.event_id
                LEFT JOIN spaces s ON c.space_id = s.space_id
                WHERE c.card_id IN ({placeholders})""",
            source_cards,
        )
        for row in rows:
            card = _row_to_card(row).model_dump()
            card["bucket"] = _bucket_for(
                row["status"], row["source_platform"], row["source_raw_event_type"]
            )
            card["platform"] = (row["source_platform"] or "unknown").lower()
            cards.append(card)
            found_ids.add(row["card_id"])

        # Preserve the snapshot's own card order so the evidence list is stable
        # across reloads regardless of how SQLite returned the rows.
        order = {cid: i for i, cid in enumerate(source_cards)}
        cards.sort(key=lambda c: order.get(c["card_id"], 1_000_000))

    lineage = await _build_lineage(
        db, space_id, ver, found_section, found_item, load_settings().get("omni", {})
    )

    # Share of today — the same local-day window /events/day uses.
    local_tz = _safe_zone(tz)
    now_local = datetime.now(local_tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    params: list = [db_ts(day_start.astimezone(timezone.utc))]
    where = "timestamp >= ?"
    space_ids = [s.strip() for s in (space_id or "").split(",") if s.strip()]
    if space_ids:
        placeholders = ",".join("?" for _ in space_ids)
        where += f" AND space_id IN ({placeholders})"
        params.extend(space_ids)
    day_rows = await db.execute_fetchall(
        f"SELECT COUNT(*) AS cnt FROM events WHERE {where}", params
    )
    day_events = day_rows[0]["cnt"] if day_rows else 0

    return {
        "item": found_item,
        "section": found_section,
        "version": ver,
        "generated_at": meta.get("generated_at"),
        "snapshot_type": meta.get("snapshot_type"),
        "cards": cards,
        "missing_card_ids": [c for c in source_cards if c not in found_ids],
        "lineage": lineage,
        "share_of_day": {
            "cards": len(source_cards),
            "day_events": day_events,
            "ratio": round(len(source_cards) / day_events, 4) if day_events else 0.0,
        },
    }


@router.get("/omni/item/lineage")
async def get_omni_item_lineage(
    space_id: str = "default",
    v: int | None = None,
    section: str | None = None,
    item: str = "",
):
    """Lineage alone, for callers that already hold the item and its cards."""
    from laya.pipeline.omni import _load_full_snapshot

    if not item:
        raise HTTPException(status_code=400, detail="item (item_key) is required")

    db = await get_db()
    content, ver, _card_ids, _meta = await _load_full_snapshot(db, space_id, v)
    if content is None:
        raise HTTPException(status_code=404, detail="No Omni snapshot for this space")

    located = _locate_item(content.get("sections", []), item, section)
    if located is None:
        raise HTTPException(status_code=404, detail=f"Item {item} not found in snapshot v{ver}")

    found_section, found_item = located
    return await _build_lineage(
        db, space_id, ver, found_section, found_item, load_settings().get("omni", {})
    )


# ---------------------------------------------------------------------------
# Pin endpoints
# ---------------------------------------------------------------------------


@router.get("/omni/pins")
async def list_pins(space_id: str = "default"):
    """List all pinned items for a space."""
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT pin_id, space_id, item_text, source_card_ids, platforms, pinned_at
           FROM omni_pins
           WHERE space_id = ?
           ORDER BY pinned_at DESC""",
        (space_id,),
    )

    return {
        "space_id": space_id,
        "pins": [
            {
                "pin_id": row["pin_id"],
                "space_id": row["space_id"],
                "item_text": row["item_text"],
                "source_card_ids": json.loads(row["source_card_ids"]),
                "platforms": json.loads(row["platforms"]),
                "pinned_at": row["pinned_at"],
            }
            for row in rows
        ],
    }


@router.post("/omni/pin")
async def pin_item(req: PinRequest):
    """Pin an item to survive compression."""
    db = await get_db()

    pin_id = f"pin_{uuid.uuid4().hex[:12]}"
    now = db_now()

    await db.execute(
        """INSERT INTO omni_pins
           (pin_id, space_id, item_text, source_card_ids, platforms, pinned_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            pin_id,
            req.space_id,
            req.text,
            json.dumps(req.source_cards),
            json.dumps(req.platforms),
            now,
        ),
    )
    await db.commit()

    log.info("omni_item_pinned", pin_id=pin_id, space_id=req.space_id)

    return {
        "pin_id": pin_id,
        "space_id": req.space_id,
        "item_text": req.text,
        "source_card_ids": req.source_cards,
        "platforms": req.platforms,
        "pinned_at": now,
    }


@router.delete("/omni/pin/{pin_id}")
async def unpin_item(pin_id: str):
    """Remove a pin."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT pin_id FROM omni_pins WHERE pin_id = ?", (pin_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Pin not found")

    await db.execute("DELETE FROM omni_pins WHERE pin_id = ?", (pin_id,))
    await db.commit()

    log.info("omni_item_unpinned", pin_id=pin_id)

    return {"status": "ok", "pin_id": pin_id}


# ---------------------------------------------------------------------------
# Bookmark endpoints
# ---------------------------------------------------------------------------


@router.post("/omni/bookmark")
async def toggle_bookmark(req: BookmarkRequest):
    """Toggle bookmark on an item in the latest snapshot.

    Bookmarks live inside the snapshot JSON — they die when the item is
    distilled away during resynthesis. Handles both full and delta rows.
    """
    from laya.pipeline.omni import _latest_cache

    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT snapshot_id, content_json, is_delta
           FROM omni_snapshots
           WHERE space_id = ?
           ORDER BY version DESC
           LIMIT 1""",
        (req.space_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No snapshot found")

    snapshot_id = rows[0]["snapshot_id"]
    is_delta = rows[0]["is_delta"]

    if is_delta:
        # Delta row — store bookmark override in the delta JSON
        delta = json.loads(rows[0]["content_json"])
        delta.setdefault("bookmark_overrides", {})[req.source_card_id] = req.bookmarked
        await db.execute(
            "UPDATE omni_snapshots SET content_json = ? WHERE snapshot_id = ?",
            (json.dumps(delta), snapshot_id),
        )
    else:
        # Full snapshot — walk sections and flip the boolean directly
        content = json.loads(rows[0]["content_json"])
        sections = content.get("sections", [])

        found = False
        for section in sections:
            for item in section.get("items", []):
                cards = item.get("source_cards", [])
                if cards and cards[0] == req.source_card_id:
                    item["bookmarked"] = req.bookmarked
                    found = True
                    break
            if found:
                break

        if not found:
            raise HTTPException(status_code=404, detail="Item not found in snapshot")

        content["sections"] = sections
        await db.execute(
            "UPDATE omni_snapshots SET content_json = ? WHERE snapshot_id = ?",
            (json.dumps(content), snapshot_id),
        )

    await db.commit()

    # Invalidate cache so next read reconstructs with the bookmark change
    _latest_cache.pop(req.space_id, None)

    log.info(
        "omni_item_bookmark_toggled",
        source_card_id=req.source_card_id,
        bookmarked=req.bookmarked,
        space_id=req.space_id,
    )

    return {"status": "ok", "bookmarked": req.bookmarked}
