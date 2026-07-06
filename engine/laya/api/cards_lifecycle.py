# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Card lifecycle + detail endpoints (split from cards_api — P7-6)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from laya.agents.session_manager import cancel_sessions_for_card
from laya.api.cards_common import _row_to_card
from laya.api.websocket import manager
from laya.db.sqlite import get_db, transaction
from laya.db.timeutil import db_now
from laya.llm.client import log_to_audit
from laya.models.card import (
    CardResponse, TagAssignment,
)
from laya.pipeline.summarize import trigger_summary_status_update
from laya.tasks import create_task as create_tracked_task

log = structlog.get_logger()
router = APIRouter()


@router.post("/cards/group/{entity_id:path}/dismiss-all")
async def dismiss_group(entity_id: str) -> dict:
    """Dismiss all non-terminal cards in a group (entity or context group)."""
    db = await get_db()

    from laya.models.card_lifecycle import INACTIVE_STATUSES, transition_card_status
    # Context groups (ctx_ ids) are keyed by context_id, not entity_id. Handle
    # both so dismissing a context group actually dismisses its cards — this was
    # asymmetric with mark_group_read, a latent trap (review §2 API — P4-15).
    # group_col is a controlled identifier, not user input.
    group_col = "context_id" if entity_id.startswith("ctx_") else "entity_id"
    # Exclude every inactive status (incl. archived) from the canonical SSOT —
    # archived cards can't be dismissed anyway, so selecting them only wastes a
    # transition attempt.
    inactive = tuple(INACTIVE_STATUSES)
    placeholders = ",".join("?" for _ in inactive)
    rows = await db.execute_fetchall(
        f"SELECT card_id, status FROM action_cards WHERE {group_col} = ? AND status NOT IN ({placeholders})",
        (entity_id, *inactive),
    )

    dismissed = 0
    for row in rows:
        try:
            await transition_card_status(row["card_id"], "dismissed", actor="user")
            dismissed += 1
        except ValueError:
            continue

    # Update summary for each dismissed card so items get strikethrough
    header_rows = await db.execute_fetchall(
        f"SELECT card_id, header FROM action_cards WHERE {group_col} = ? AND status = 'dismissed'",
        (entity_id,),
    )
    for hr in header_rows:
        create_tracked_task(
            trigger_summary_status_update(hr["card_id"], hr["header"], "dismissed"),
            name=f"summary_status_{hr['card_id']}",
        )

    now = db_now()
    await db.execute(
        f"UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE {group_col} = ? AND read_at IS NULL",
        (now, entity_id),
    )
    await db.commit()

    log.info("group_dismissed", entity_id=entity_id, count=dismissed)
    return {"dismissed": dismissed, "entity_id": entity_id}


@router.post("/cards/{card_id}/archive")
async def archive_card(card_id: str) -> dict:
    """Archive an action card."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    current = rows[0]["status"]
    if current == "archived":
        return {"status": "archived", "card_id": card_id}

    # Cancel any running agent session before archiving (don't block the HTTP response)
    try:
        await asyncio.wait_for(cancel_sessions_for_card(card_id), timeout=2.0)
    except asyncio.TimeoutError:
        log.warning("session_cancel_timeout", card_id=card_id)

    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(card_id, "archived", actor="user")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    now = db_now()
    await db.execute(
        "UPDATE action_cards SET read_at = COALESCE(read_at, ?) WHERE card_id = ?",
        (now, card_id),
    )
    await db.commit()

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "archive", "previous_status": current},
    )

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "archived"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "archived", "card_id": card_id}


@router.post("/cards/{card_id}/reopen")
async def reopen_card(card_id: str) -> dict:
    """Reopen a card, retrying the last failed stage when applicable.

    Retry strategy based on failed_stage:
    - agent_spawn / agent_execution: reset to ready so user can re-invoke agent
    - action_execution: reset to ready so user can re-execute the action
    - pipeline / NULL: reset to pending for full reprocessing
    - Archived/done/dismissed cards with previous_status: restore that status
    - Fallback: reset to pending
    """
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status, failed_stage, agent_prompt, persona, space_id, previous_status FROM action_cards WHERE card_id = ?",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    row = rows[0]
    current = row["status"]
    reopenable = {"dismissed", "done", "failed", "archived", "agent_running"}
    if current not in reopenable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{current}' cannot be reopened"
        )

    failed_stage = row["failed_stage"] if current == "failed" else None

    # Decide the reopen target. Reopen is a deliberate non-forward restore/retry,
    # so it goes through the lifecycle SSOT with allow_restore=True — a single
    # write + broadcast + atomic status guard — instead of the four near-identical
    # raw UPDATE/commit/broadcast blocks that used to live here (review §5.4 — P7-4).
    restoring = False
    log_extra: dict = {}
    if failed_stage in ("agent_spawn", "agent_execution") and row["agent_prompt"]:
        new_status = "ready"  # agent failed — let the user re-invoke it
        log_extra = {"retry_stage": failed_stage}
    elif failed_stage == "action_execution":
        new_status = "ready"  # action failed — let the user re-execute
        log_extra = {"retry_stage": failed_stage}
    elif row["previous_status"]:
        new_status = row["previous_status"]  # restore the saved pre-terminal status
        restoring = True
        log_extra = {"previous_status": row["previous_status"]}
    else:
        new_status = "pending"  # no context — full reprocess
        log_extra = {"retry_stage": failed_stage}

    from laya.models.card_lifecycle import transition_card_status
    try:
        await transition_card_status(
            card_id, new_status, actor="user",
            save_previous=False,
            allow_restore=True,
            # A non-terminal target already clears resolved_at/failed_stage in the
            # SSOT; also clear the consumed previous_status on a restore.
            extra_fields={"previous_status": None} if restoring else None,
        )
    except ValueError as e:
        # Status changed under us (atomic guard) — surface as a conflict.
        raise HTTPException(status_code=409, detail=str(e))

    log.info("card_reopened", card_id=card_id, new_status=new_status, **log_extra)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], new_status),
            name=f"summary_status_{card_id}",
        )

    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "reopen", "previous_status": current,
                  "new_status": new_status, "failed_stage": failed_stage},
    )

    return {"status": new_status, "card_id": card_id}


@router.get("/cards/{card_id}")
async def get_card(card_id: str) -> CardResponse:
    """Get full action card detail."""
    # Normalize: chat LLM may reference cards without the "card_" prefix
    if not card_id.startswith("card_"):
        card_id = f"card_{card_id}"
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT c.card_id, c.event_id, c.created_at, c.priority, c.persona, c.category,
                  c.header, c.summary, c.intelligence, c.staged_output, c.suggested_actions,
                  c.status, c.privacy_tier, c.has_workspace, c.resolved_at, c.user_feedback,
                  c.feedback_type, c.confidence, c.router_model, c.stager_model, c.updated_at,
                  c.entity_id, c.source_ref, c.source_url, c.selected_action_id,
                  c.space_id, c.bookmarked_at, c.read_at, c.group_active_at,
                  e.actor_name, e.actor_email,
                  s.name AS space_name, s.color AS space_color
           FROM action_cards c
           LEFT JOIN events e ON c.event_id = e.event_id
           LEFT JOIN spaces s ON c.space_id = s.space_id
           WHERE c.card_id = ?""",
        (card_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    card = _row_to_card(rows[0])
    # Hydrate tags
    tag_rows = await db.execute_fetchall(
        """SELECT t.tag_id, t.name AS tag_name, t.color, t.is_system, ta.assigned_by
           FROM tag_assignments ta JOIN tags t ON ta.tag_id = t.tag_id
           WHERE ta.target_type = 'card' AND ta.target_id = ?
           ORDER BY t.name""",
        (card_id,),
    )
    card.tags = [TagAssignment(**dict(r)) for r in tag_rows]
    return card


class SourceEventResponse(BaseModel):
    """The original event that produced a card — raw body + platform metadata."""

    event_id: str
    platform: str
    raw_event_type: str
    timestamp: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    actor_handle: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_title: str | None = None
    subject_url: str | None = None
    body: str | None = None
    metadata: dict[str, Any] = {}


@router.get("/cards/{card_id}/source-event")
async def get_card_source_event(card_id: str) -> SourceEventResponse:
    """Return the original ingested event behind a card (raw message + metadata).

    Powers the "Show original content" modal. The full event is always stored at
    ingest time (events.content_body / content_metadata), so no LLM call or
    re-fetch is needed — we just join back through action_cards.event_id.
    """
    if not card_id.startswith("card_"):
        card_id = f"card_{card_id}"
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT e.event_id, e.source_platform, e.source_raw_event_type, e.timestamp,
                  e.actor_name, e.actor_email, e.actor_handle,
                  e.subject_type, e.subject_id, e.subject_title, e.subject_url,
                  e.content_body, e.content_metadata
           FROM action_cards c JOIN events e ON c.event_id = e.event_id
           WHERE c.card_id = ?""",
        (card_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Source event not found")

    row = rows[0]
    metadata: dict[str, Any] = {}
    if row["content_metadata"]:
        try:
            parsed = json.loads(row["content_metadata"])
            if isinstance(parsed, dict):
                metadata = parsed
        except (json.JSONDecodeError, TypeError):
            pass  # malformed metadata shouldn't break the modal — show body only

    return SourceEventResponse(
        event_id=row["event_id"],
        platform=row["source_platform"],
        raw_event_type=row["source_raw_event_type"],
        timestamp=row["timestamp"],
        actor_name=row["actor_name"],
        actor_email=row["actor_email"],
        actor_handle=row["actor_handle"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        subject_title=row["subject_title"],
        subject_url=row["subject_url"],
        body=row["content_body"],
        metadata=metadata,
    )


@router.post("/cards/{card_id}/done")
async def mark_card_done(card_id: str) -> dict:
    """Mark an action card as done (user has reviewed/acted on it)."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    doneable = {"pending", "ready", "awaiting_input"}
    if rows[0]["status"] not in doneable:
        raise HTTPException(
            status_code=409, detail=f"Card status '{rows[0]['status']}' cannot be marked done"
        )

    current = rows[0]["status"]
    now = db_now()
    await db.execute(
        """UPDATE action_cards
           SET status = 'done', previous_status = ?, resolved_at = ?, updated_at = ?,
               read_at = COALESCE(read_at, ?)
           WHERE card_id = ?""",
        (current, now, now, now, card_id),
    )
    await db.commit()

    await manager.broadcast(
        {"type": "card_updated", "card_id": card_id, "payload": {"status": "done"}}
    )

    # Audit the lifecycle change to match the dismiss/archive/reopen endpoints, which
    # all write a "lifecycle" entry — done was the lone gap.
    await log_to_audit(
        event_id=None, card_id=card_id, step="lifecycle",
        model="n/a", input_tokens=0, output_tokens=0, latency_ms=0,
        success=True,
        metadata={"action": "done", "previous_status": current},
    )

    log.info("card_done", card_id=card_id)

    # Update daily summary with status change
    header_rows = await db.execute_fetchall(
        "SELECT header FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if header_rows:
        create_tracked_task(
            trigger_summary_status_update(card_id, header_rows[0]["header"], "done"),
            name=f"summary_status_{card_id}",
        )

    return {"status": "done", "card_id": card_id}



async def clear_stale_polishing_flags() -> int:
    """Clear any `_polishing=True` flags left on action payloads by an engine
    restart mid-polish. Without this sweep the affected action 409s forever and
    the UI shows an eternal spinner (review §2 API — P4-14). Called at startup."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT card_id, suggested_actions FROM action_cards "
        "WHERE suggested_actions LIKE '%_polishing%'"
    )
    cleared = 0
    for row in rows:
        try:
            actions = json.loads(row["suggested_actions"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        changed = False
        for a in actions:
            p = a.get("payload")
            if isinstance(p, dict) and p.get("_polishing"):
                p["_polishing"] = False
                changed = True
        if changed:
            await db.execute(
                "UPDATE action_cards SET suggested_actions = ? WHERE card_id = ?",
                (json.dumps(actions), row["card_id"]),
            )
            cleared += 1
    if cleared:
        await db.commit()
        log.info("polishing_flags_cleared", count=cleared)
    return cleared


async def _delete_card_cascade(db, card_id: str, event_id: str | None) -> None:
    """Hard-delete a card and all its related rows in correct FK order.

    The SQL cascade runs inside one guarded transaction so it commits all-or-
    nothing and rolls back on a mid-sequence failure — otherwise a partial
    cascade (e.g. child rows gone, card row left) would be flushed by the next
    commit() elsewhere (review §2 API — P4-12). Self-commits, so callers must not
    also commit. The vector-embedding cleanup runs AFTER the block, outside the
    write lock, since it's an external 2s-timeout call.
    """
    async with transaction():
        # workspace_events → workspace_sessions → action_log → audit_log → action_cards → events
        await db.execute(
            "DELETE FROM workspace_events WHERE session_id IN "
            "(SELECT session_id FROM workspace_sessions WHERE card_id = ?)",
            (card_id,),
        )
        await db.execute("DELETE FROM workspace_sessions WHERE card_id = ?", (card_id,))
        await db.execute("DELETE FROM action_log WHERE card_id = ?", (card_id,))
        await db.execute("DELETE FROM audit_log WHERE card_id = ?", (card_id,))
        await db.execute("DELETE FROM action_cards WHERE card_id = ?", (card_id,))

        # Polymorphic tag assignments have no FK to action_cards, so they orphan on
        # hard-delete unless cleaned explicitly (review §2 API — P4-17).
        await db.execute(
            "DELETE FROM tag_assignments WHERE target_type = 'card' AND target_id = ?",
            (card_id,),
        )

        # Rolling group summaries embed card_ids as a JSON array (no FK); drop the
        # deleted id so card_count stays truthful, and delete the summary outright
        # if it held only this card (review §2 API — P4-17).
        summary_rows = await db.execute_fetchall(
            "SELECT entity_id, card_ids FROM group_summaries WHERE card_ids LIKE ?",
            (f'%"{card_id}"%',),
        )
        for srow in summary_rows:
            try:
                ids = json.loads(srow["card_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if card_id not in ids:
                continue
            ids = [c for c in ids if c != card_id]
            if ids:
                await db.execute(
                    "UPDATE group_summaries SET card_ids = ?, card_count = ? WHERE entity_id = ?",
                    (json.dumps(ids), len(ids), srow["entity_id"]),
                )
            else:
                await db.execute(
                    "DELETE FROM group_summaries WHERE entity_id = ?", (srow["entity_id"],)
                )

        # Remove the source event only if no other cards still reference it
        if event_id:
            await db.execute(
                "DELETE FROM events WHERE event_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM action_cards WHERE event_id = ?)",
                (event_id, event_id),
            )

    # Remove the card's vector embedding (best-effort), OUTSIDE the guarded section
    # so the 2s external call never holds the write lock. Housekeeping calls this
    # cascade directly, so the ChromaDB delete must live here — not only in the
    # HTTP delete path — or retention never reclaims vectors (review §1.2).
    try:
        from laya.db.chromadb_store import delete_document
        await asyncio.wait_for(delete_document(card_id), timeout=2.0)
    except Exception as e:
        log.warning("card_embed_delete_failed", card_id=card_id, error=str(e))


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str) -> dict:
    """Permanently delete an archived card and all related data."""
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT status, event_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")

    if rows[0]["status"] != "archived":
        raise HTTPException(
            status_code=409, detail="Only archived cards can be deleted"
        )

    # Cancel any lingering agent session (don't block the HTTP response)
    try:
        await asyncio.wait_for(cancel_sessions_for_card(card_id), timeout=2.0)
    except asyncio.TimeoutError:
        log.warning("session_cancel_timeout_on_delete", card_id=card_id)

    event_id = rows[0]["event_id"]
    # _delete_card_cascade self-commits its guarded transaction (P4-12) — no
    # commit() here. ChromaDB embedding, tag assignments and group-summary refs
    # are cleaned inside the cascade (shared with the housekeeping path).
    await _delete_card_cascade(db, card_id, event_id)

    # Clean up research directory if it exists (best-effort)
    from laya.config import LAYA_HOME
    research_dir = LAYA_HOME / "tmp" / "research" / card_id
    if research_dir.exists():
        try:
            import shutil
            shutil.rmtree(research_dir)
        except Exception as e:
            log.warning("research_dir_cleanup_failed", card_id=card_id, error=str(e))

    await manager.broadcast({"type": "card_deleted", "card_id": card_id})
    log.info("card_deleted", card_id=card_id)
    return {"status": "deleted", "card_id": card_id}
