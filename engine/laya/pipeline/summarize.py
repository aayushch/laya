# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""SUMMARIZE pipeline step — maintain a running daily summary of processed cards, per space."""

import asyncio
import json
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.config import get_debounce_config
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import llm_call
from laya.llm.prompts.summarizer import (
    build_batch_summarizer_messages,
    build_summarizer_messages,
    get_summarizer_json_schema,
)

_COMPACTION_THRESHOLD = 40
_SUMMARY_MAX_TOKENS = 65536

log = structlog.get_logger()

# Per-space debounce state.
_debounce_lock = asyncio.Lock()
_pending_cards: dict[str, list[dict]] = {}
_pending_status_changes: dict[str, list[dict]] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}

# Per-space run locks. A 90s flush can pop its batch and start _run_summary_update
# while a subsequent flush is scheduled; without serialization the two overlap and
# lost-update the daily summary read-modify-write (review §2 pipeline / §3.4).
_space_run_locks: dict[str, asyncio.Lock] = {}


def _get_run_lock(space_id: str) -> asyncio.Lock:
    # Safe without a guard: creation is synchronous (no await between get/set).
    lock = _space_run_locks.get(space_id)
    if lock is None:
        lock = asyncio.Lock()
        _space_run_locks[space_id] = lock
    return lock


def _get_debounce_seconds() -> float:
    """Read daily summary debounce interval from settings."""
    cfg = get_debounce_config()
    return cfg.get("daily_summary_seconds", 90)


def _get_batch_max_cards() -> int:
    """Max cards folded into a single daily-summary LLM call. A flush with more
    fresh cards than this is split into ceil(N/K) batched calls so a large burst
    can't overflow a small local context window."""
    cfg = get_debounce_config()
    k = cfg.get("daily_summary_batch_max_cards", 10)
    return max(1, int(k))


async def trigger_summary_update(
    card_id: str,
    card_header: str,
    card_summary: str,
    card_priority: str,
    card_category: str,
    space_id: str,
    card_persona: str | None = None,
    card_intelligence: list[str] | None = None,
    actor_name: str | None = None,
    source_platform: str | None = None,
    card_tags: list[str] | None = None,
) -> None:
    """Queue a new card for summary incorporation (debounced, per-space)."""
    card_data = {
        "card_id": card_id,
        "card_header": card_header,
        "card_summary": card_summary,
        "card_priority": card_priority,
        "card_category": card_category,
        "card_persona": card_persona,
        "card_intelligence": card_intelligence,
        "actor_name": actor_name,
        "source_platform": source_platform,
        "card_tags": card_tags,
    }

    async with _debounce_lock:
        _pending_cards.setdefault(space_id, []).append(card_data)
        task = _debounce_tasks.get(space_id)
        if task and not task.done():
            task.cancel()
        from laya.tasks import create_task as create_tracked_task
        _debounce_tasks[space_id] = create_tracked_task(_debounced_run(space_id))


async def trigger_summary_status_update(
    card_id: str,
    card_header: str,
    new_status: str,
) -> None:
    """Queue a status change for summary update (debounced, per-space).

    Resolves space_id from the card's DB record so callers don't need to
    thread it through.
    """
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT space_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    space_id = rows[0]["space_id"] if rows else "default"

    change_data = {
        "card_id": card_id,
        "card_header": card_header,
        "new_status": new_status,
    }

    async with _debounce_lock:
        _pending_status_changes.setdefault(space_id, []).append(change_data)
        task = _debounce_tasks.get(space_id)
        if task and not task.done():
            task.cancel()
        from laya.tasks import create_task as create_tracked_task
        _debounce_tasks[space_id] = create_tracked_task(_debounced_run(space_id))


async def _debounced_run(space_id: str) -> None:
    """Wait for the debounce period, then process pending updates for one space."""
    try:
        await asyncio.sleep(_get_debounce_seconds())
    except asyncio.CancelledError:
        return

    async with _debounce_lock:
        cards = list(_pending_cards.pop(space_id, []))
        status_changes = list(_pending_status_changes.pop(space_id, []))
        _debounce_tasks.pop(space_id, None)

    if not cards and not status_changes:
        return

    try:
        # Serialize per space so an overlapping flush can't lost-update the
        # daily summary (review §2 pipeline / §3.4).
        async with _get_run_lock(space_id):
            await _run_summary_update(space_id, cards, status_changes)
    except Exception as e:
        log.error("summary_update_failed", space_id=space_id, error=str(e))


def _count_items(summary: dict) -> int:
    """Count total items across all summary sections."""
    return sum(
        len(summary.get(section, []))
        for section in ("events_and_meetings", "action_items", "key_updates")
    )


def _compact_summary(summary: dict) -> dict:
    """Compact a summary by removing resolved items that are least important.

    Keeps all pending items intact. For done/dismissed/archived items, removes
    LOW and MEDIUM priority ones first, keeping CRITICAL and HIGH. This reduces
    the token footprint so the LLM has room for new items.
    """
    compacted = {}
    removed = 0

    for section in ("events_and_meetings", "action_items", "key_updates"):
        items = summary.get(section, [])
        kept = []
        for item in items:
            status = item.get("status", "pending")
            priority = item.get("priority", "MEDIUM")
            if status in ("pending", "ready") or priority in ("CRITICAL", "HIGH"):
                kept.append(item)
            else:
                removed += 1
        compacted[section] = kept

    if removed > 0:
        log.info("summary_compacted", removed_items=removed, remaining=_count_items(compacted))

    return compacted


async def _fold_batch(
    current_summary: dict | None,
    chunk: list[dict],
    schema: dict,
    space_id: str,
    today: str,
) -> dict:
    """Fold a chunk of new cards into the summary in a SINGLE llm_call.

    Raises on a malformed/empty response so the caller can fall back to the
    per-card path. The `card_id` passed to llm_call is only used for audit
    logging, so a representative id (the chunk's first) is fine.
    """
    messages = build_batch_summarizer_messages(current_summary=current_summary, new_cards=chunk)
    response = await llm_call(
        role="stager",
        messages=messages,
        response_schema=schema,
        card_id=chunk[0]["card_id"],
        step="summarize",
        temperature=0.2,
        max_tokens=_SUMMARY_MAX_TOKENS,
        space_id=space_id,
    )
    if not response.parsed:
        truncation_hint = " (response was truncated)" if response.truncated else ""
        raise ValueError(
            f"LLM returned malformed JSON for batch summary{truncation_hint} "
            f"(output_tokens={response.output_tokens}, model={response.model})"
        )
    return response.parsed


async def _fold_cards_sequentially(
    current_summary: dict | None,
    cards: list[dict],
    schema: dict,
    space_id: str,
    today: str,
) -> tuple[dict | None, list[str]]:
    """Fold cards one at a time (the pre-batching path), used as the fallback when a
    batched fold fails. Returns the updated summary and the card_ids actually
    incorporated. A single card's failure is logged and skipped — that card is
    dropped for the day, matching the original per-card behavior.
    """
    incorporated: list[str] = []
    for card in cards:
        messages = build_summarizer_messages(
            current_summary=current_summary,
            card_header=card["card_header"],
            card_summary=card["card_summary"],
            card_priority=card["card_priority"],
            card_category=card["card_category"],
            card_id=card["card_id"],
            card_intelligence=card.get("card_intelligence"),
            card_persona=card.get("card_persona"),
            actor_name=card.get("actor_name"),
            source_platform=card.get("source_platform"),
            card_tags=card.get("card_tags"),
        )
        try:
            response = await llm_call(
                role="stager",
                messages=messages,
                response_schema=schema,
                card_id=card["card_id"],
                step="summarize",
                temperature=0.2,
                max_tokens=_SUMMARY_MAX_TOKENS,
                space_id=space_id,
            )
            if not response.parsed:
                truncation_hint = " (response was truncated)" if response.truncated else ""
                raise ValueError(
                    f"LLM returned malformed JSON for summary{truncation_hint} "
                    f"(output_tokens={response.output_tokens}, model={response.model})"
                )
            current_summary = response.parsed
            incorporated.append(card["card_id"])
            log.info("summary_card_incorporated", card_id=card["card_id"], space_id=space_id, date=today)
        except Exception as e:
            log.error("summary_llm_failed", card_id=card["card_id"], space_id=space_id, error=str(e))
            continue
    return current_summary, incorporated


async def _run_summary_update(
    space_id: str,
    new_cards: list[dict],
    status_changes: list[dict],
) -> None:
    """Run the actual summary update for a single space: fetch → LLM augment → upsert → broadcast."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT summary_json, card_ids FROM daily_summaries WHERE date = ? AND space_id = ?",
        (today, space_id),
    )

    current_summary: dict | None = None
    existing_card_ids: list[str] = []
    if rows:
        try:
            current_summary = json.loads(rows[0]["summary_json"])
            existing_card_ids = json.loads(rows[0]["card_ids"])
        except (json.JSONDecodeError, KeyError):
            pass

    schema = get_summarizer_json_schema()

    # Filter to cards not already folded in: dedup against the persisted card_ids
    # plus intra-flush duplicates (the same card queued twice within one debounce
    # window). Without this a re-run or a duplicate emit would double-count a card.
    seen: set[str] = set()
    fresh: list[dict] = []
    for card in new_cards:
        cid = card["card_id"]
        if cid in existing_card_ids or cid in seen:
            continue
        seen.add(cid)
        fresh.append(card)

    # Fold the fresh cards in bounded batches — ONE llm_call per chunk instead of
    # one per card. This is the whole optimization: a burst of N cards costs
    # ceil(N/K) inferences, not N. The per-call cap keeps a big burst from
    # overflowing a small local context window; compaction runs before each chunk
    # so the summary (the dominant token term) stays bounded as it grows.
    batch_max = _get_batch_max_cards()
    for start in range(0, len(fresh), batch_max):
        chunk = fresh[start:start + batch_max]

        if current_summary and _count_items(current_summary) >= _COMPACTION_THRESHOLD:
            current_summary = _compact_summary(current_summary)

        try:
            current_summary = await _fold_batch(current_summary, chunk, schema, space_id, today)
            existing_card_ids.extend(c["card_id"] for c in chunk)
            log.info("summary_batch_incorporated", count=len(chunk), space_id=space_id, date=today)
        except Exception as e:
            # Batch fold failed (e.g. malformed/truncated JSON) — fall back to the
            # proven per-card path for THIS chunk only. Worst case degrades to the
            # pre-batching behavior, never worse; no unbounded retry.
            log.warning(
                "summary_batch_failed_fallback_sequential",
                count=len(chunk),
                space_id=space_id,
                error=str(e),
            )
            current_summary, incorporated = await _fold_cards_sequentially(
                current_summary, chunk, schema, space_id, today
            )
            existing_card_ids.extend(incorporated)

    for change in status_changes:
        if not current_summary:
            break

        cid = change["card_id"]
        new_status = change["new_status"]
        matched = False

        for section in ("events_and_meetings", "action_items", "key_updates"):
            for item in current_summary.get(section, []):
                if item.get("card_id") == cid:
                    item["status"] = new_status
                    matched = True

        if matched:
            log.info(
                "summary_status_updated",
                card_id=cid,
                new_status=new_status,
                space_id=space_id,
                date=today,
            )
        else:
            log.warning(
                "summary_status_card_not_found",
                card_id=cid,
                card_header=change["card_header"],
                new_status=new_status,
                space_id=space_id,
                date=today,
            )

    if current_summary:
        summary_json = json.dumps(current_summary)
        card_ids_json = json.dumps(existing_card_ids)
        now = db_now()

        await db.execute(
            """INSERT INTO daily_summaries (date, space_id, summary_json, card_ids, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(date, space_id) DO UPDATE SET
                   summary_json = excluded.summary_json,
                   card_ids = excluded.card_ids,
                   updated_at = excluded.updated_at""",
            (today, space_id, summary_json, card_ids_json, now),
        )
        await db.commit()

        await manager.broadcast(
            {
                "type": "summary_updated",
                "payload": {
                    "date": today,
                    "space_id": space_id,
                    "summary": current_summary,
                    "updated_at": now,
                },
            }
        )

        log.info("daily_summary_persisted", date=today, space_id=space_id, card_count=len(existing_card_ids))
