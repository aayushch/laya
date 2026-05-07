"""SUMMARIZE pipeline step — maintain a running daily summary of processed cards, per space."""

import asyncio
import json
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.config import get_debounce_config
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.summarizer import (
    build_status_change_messages,
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


def _get_debounce_seconds() -> float:
    """Read daily summary debounce interval from settings."""
    cfg = get_debounce_config()
    return cfg.get("daily_summary_seconds", 30)


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

    if current_summary and _count_items(current_summary) >= _COMPACTION_THRESHOLD:
        current_summary = _compact_summary(current_summary)

    for card in new_cards:
        if card["card_id"] in existing_card_ids:
            continue

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

            existing_card_ids.append(card["card_id"])
            log.info("summary_card_incorporated", card_id=card["card_id"], space_id=space_id, date=today)

        except Exception as e:
            log.error(
                "summary_llm_failed",
                card_id=card["card_id"],
                space_id=space_id,
                error=str(e),
            )
            continue

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
        now = datetime.now(timezone.utc).isoformat()

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
