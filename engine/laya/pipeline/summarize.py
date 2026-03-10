"""SUMMARIZE pipeline step — maintain a running daily summary of processed cards."""

import asyncio
import json
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.summarizer import (
    build_status_change_messages,
    build_summarizer_messages,
    get_summarizer_json_schema,
)

log = structlog.get_logger()

# Debounce state: accumulate card_ids and only run after a quiet period.
_debounce_lock = asyncio.Lock()
_pending_cards: list[dict] = []
_pending_status_changes: list[dict] = []
_debounce_task: asyncio.Task | None = None
_DEBOUNCE_SECONDS = 5


async def trigger_summary_update(
    card_id: str,
    card_header: str,
    card_summary: str,
    card_priority: str,
    card_category: str,
    card_persona: str | None = None,
    card_intelligence: list[str] | None = None,
    actor_name: str | None = None,
    source_platform: str | None = None,
) -> None:
    """Queue a new card for summary incorporation (debounced)."""
    global _debounce_task

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
        _pending_cards.append(card_data)
        if _debounce_task and not _debounce_task.done():
            _debounce_task.cancel()
        _debounce_task = asyncio.create_task(_debounced_run())


async def trigger_summary_status_update(
    card_id: str,
    card_header: str,
    new_status: str,
) -> None:
    """Queue a status change for summary update (debounced)."""
    global _debounce_task

    change_data = {
        "card_id": card_id,
        "card_header": card_header,
        "new_status": new_status,
    }

    async with _debounce_lock:
        _pending_status_changes.append(change_data)
        if _debounce_task and not _debounce_task.done():
            _debounce_task.cancel()
        _debounce_task = asyncio.create_task(_debounced_run())


async def _debounced_run() -> None:
    """Wait for the debounce period, then process all pending updates."""
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # Another update came in, timer reset

    async with _debounce_lock:
        cards = list(_pending_cards)
        status_changes = list(_pending_status_changes)
        _pending_cards.clear()
        _pending_status_changes.clear()

    if not cards and not status_changes:
        return

    try:
        await _run_summary_update(cards, status_changes)
    except Exception as e:
        log.error("summary_update_failed", error=str(e))


async def _run_summary_update(
    new_cards: list[dict],
    status_changes: list[dict],
) -> None:
    """Run the actual summary update: fetch current → LLM augment → upsert → broadcast."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = await get_db()

    # Fetch current summary
    rows = await db.execute_fetchall(
        "SELECT summary_json, card_ids FROM daily_summaries WHERE date = ?",
        (today,),
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

    # Process new cards one at a time (each builds on previous)
    for card in new_cards:
        if card["card_id"] in existing_card_ids:
            continue  # Already incorporated

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
                max_tokens=4000,
            )

            if not response.parsed:
                raise ValueError("LLM returned malformed JSON for summary")
            current_summary = response.parsed

            existing_card_ids.append(card["card_id"])
            log.info("summary_card_incorporated", card_id=card["card_id"], date=today)

        except Exception as e:
            log.error(
                "summary_llm_failed",
                card_id=card["card_id"],
                error=str(e),
            )
            continue

    # Process status changes
    for change in status_changes:
        if not current_summary:
            break  # No summary to update

        messages = build_status_change_messages(
            current_summary=current_summary,
            card_id=change["card_id"],
            card_header=change["card_header"],
            new_status=change["new_status"],
        )

        try:
            response = await llm_call(
                role="stager",
                messages=messages,
                response_schema=schema,
                card_id=change["card_id"],
                step="summarize_status",
                temperature=0.1,
                max_tokens=4000,
            )

            if not response.parsed:
                raise ValueError("LLM returned malformed JSON for status update summary")
            current_summary = response.parsed

            log.info(
                "summary_status_updated",
                card_id=change["card_id"],
                new_status=change["new_status"],
                date=today,
            )

        except Exception as e:
            log.error(
                "summary_status_llm_failed",
                card_id=change["card_id"],
                error=str(e),
            )
            continue

    # Upsert summary into DB
    if current_summary:
        summary_json = json.dumps(current_summary)
        card_ids_json = json.dumps(existing_card_ids)
        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            """INSERT INTO daily_summaries (date, summary_json, card_ids, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   summary_json = excluded.summary_json,
                   card_ids = excluded.card_ids,
                   updated_at = excluded.updated_at""",
            (today, summary_json, card_ids_json, now),
        )
        await db.commit()

        # Broadcast update to frontend
        await manager.broadcast(
            {
                "type": "summary_updated",
                "payload": {
                    "date": today,
                    "summary": current_summary,
                    "updated_at": now,
                },
            }
        )

        log.info("daily_summary_persisted", date=today, card_count=len(existing_card_ids))
