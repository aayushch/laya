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

# Maximum number of items across all sections before compaction kicks in.
_COMPACTION_THRESHOLD = 40
_SUMMARY_MAX_TOKENS = 65536

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
    """Queue a new card for summary incorporation (debounced).

    Space metadata (space_id/name/color) is resolved from the DB at summarize
    time via a join on action_cards → spaces, not passed through from the
    caller. See _hydrate_space_fields.
    """
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
        from laya.tasks import create_task as create_tracked_task
        _debounce_task = create_tracked_task(_debounced_run())


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
        from laya.tasks import create_task as create_tracked_task
        _debounce_task = create_tracked_task(_debounced_run())


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
            # Keep all pending items and high-priority resolved items
            if status in ("pending", "ready", "requires_approval") or priority in ("CRITICAL", "HIGH"):
                kept.append(item)
            else:
                removed += 1
        compacted[section] = kept

    if removed > 0:
        log.info("summary_compacted", removed_items=removed, remaining=_count_items(compacted))

    return compacted


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

    # Build card_id → space info lookup straight from the DB for every card the
    # summary references (new + already-incorporated). The summarizer LLM no
    # longer carries space_id/name/color through its contract — we hydrate
    # deterministically from action_cards → spaces so values stay in sync with
    # DB state and don't leak into the user-visible `text` field.
    all_card_ids = list({card["card_id"] for card in new_cards} | set(existing_card_ids))
    space_lookup: dict[str, dict[str, str]] = {}
    if all_card_ids:
        placeholders = ",".join("?" for _ in all_card_ids)
        card_rows = await db.execute_fetchall(
            f"SELECT ac.card_id, ac.space_id, s.name AS space_name, s.color AS space_color "
            f"FROM action_cards ac LEFT JOIN spaces s ON ac.space_id = s.space_id "
            f"WHERE ac.card_id IN ({placeholders})",
            all_card_ids,
        )
        for row in card_rows:
            space_lookup[row["card_id"]] = {
                "space_id": row["space_id"] or "default",
                "space_name": row["space_name"] or "Default",
                "space_color": row["space_color"] or "#F97316",
            }

    def _hydrate_space_fields(summary: dict) -> dict:
        """Inject space_id/name/color onto every item from the DB-backed lookup.

        Falls back to the Default space when a card isn't found (shouldn't happen
        in practice — summary-update fires after the card is persisted — but
        keeps the UI contract stable defensively).
        """
        default_info = {"space_id": "default", "space_name": "Default", "space_color": "#F97316"}
        for section in ("events_and_meetings", "action_items", "key_updates"):
            for item in summary.get(section, []):
                info = space_lookup.get(item.get("card_id", ""), default_info)
                item["space_id"] = info["space_id"]
                item["space_name"] = info["space_name"]
                item["space_color"] = info["space_color"]
        return summary

    # Hydrate space fields on the existing summary loaded from DB
    if current_summary:
        current_summary = _hydrate_space_fields(current_summary)

    # Compact the summary if it has grown too large to avoid token truncation
    if current_summary and _count_items(current_summary) >= _COMPACTION_THRESHOLD:
        current_summary = _compact_summary(current_summary)

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
                max_tokens=_SUMMARY_MAX_TOKENS,
            )

            if not response.parsed:
                truncation_hint = " (response was truncated)" if response.truncated else ""
                raise ValueError(
                    f"LLM returned malformed JSON for summary{truncation_hint} "
                    f"(output_tokens={response.output_tokens}, model={response.model})"
                )
            current_summary = _hydrate_space_fields(response.parsed)

            existing_card_ids.append(card["card_id"])
            log.info("summary_card_incorporated", card_id=card["card_id"], date=today)

        except Exception as e:
            log.error(
                "summary_llm_failed",
                card_id=card["card_id"],
                error=str(e),
            )
            continue

    # Process status changes — programmatic find-and-replace by card_id.
    # This is deterministic and doesn't need an LLM call; previous LLM-based
    # approach was unreliable (model would sometimes miss matching the card_id
    # or alter other items).
    for change in status_changes:
        if not current_summary:
            break  # No summary to update

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
                date=today,
            )
        else:
            log.warning(
                "summary_status_card_not_found",
                card_id=cid,
                card_header=change["card_header"],
                new_status=new_status,
                date=today,
            )

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
