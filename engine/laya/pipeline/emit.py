# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""EMIT pipeline step — persist action card to SQLite, ChromaDB, and broadcast."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.config import load_settings
from laya.db.chromadb_store import embed_document
from laya.db.sqlite import get_db
from laya.db.timeutil import db_now, db_ts
from laya.llm.client import log_to_audit
from laya.models.card import ActionCardData
from laya.models.card_lifecycle import INACTIVE_STATUSES, transition_card_status
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.entity_resolution import resolve_semantic_entities
from laya.pipeline.summarize import trigger_summary_status_update, trigger_summary_update
from laya.tasks import create_task
from laya.workers.base import WorkerResult

log = structlog.get_logger()


def _event_ts(event: LayaEvent) -> str:
    """The originating event's platform time in canonical DB format.

    Cards are stamped with this — NOT wall-clock emit time — so that events
    ingested late (e.g. after a paused space is resumed) show their real time
    in the feed instead of "just now", and bucket to their true calendar date.
    The true ingest time is still recoverable from `events.created_at`.
    """
    return db_ts(event.timestamp)


# Human-readable subject type labels for embedding text
_SUBJECT_LABELS = {
    "ticket": "issue",
    "pull_request": "pull request",
    "build": "build",
    "thread": "discussion",
    "email_thread": "email thread",
    "meeting": "meeting",
    "briefing": "briefing",
}


def _build_embedding_text(
    *,
    platform: str,
    header: str,
    summary: str,
    actor_name: str,
    subject_type: str,
    category: str,
    entity_refs: str,
    thread_context: str = "",
) -> str:
    """Build a canonical embedding template for ChromaDB.

    Produces structured, platform-normalized text that maximizes cross-platform
    entity linking. Each field is explicitly labeled so the embedding model can
    weight it properly.  See notification-linking-semantic-search.md §3.1.

    ``thread_context`` situates a follow-up card within its thread (Contextual
    Embeddings). Terse update cards ("Approved.", "Resolved as Fixed.") are
    intentionally summarized without re-describing their parent (see the stager
    prompt), so on their own they lose the *semantic* referent of the thread.
    The labeled "Thread so far" clause restores it without an extra LLM call —
    see ``_fetch_thread_context``.
    """
    type_label = _SUBJECT_LABELS.get(subject_type, subject_type)
    parts = [f"{platform} notification about {header}."]
    parts.append(summary)
    if thread_context:
        parts.append(f"Thread so far: {thread_context}.")
    if actor_name:
        parts.append(f"People: {actor_name}.")
    parts.append(f"Action: {category}, {type_label}.")
    if entity_refs:
        parts.append(f"Identifiers: {entity_refs}.")
    return " ".join(parts)


async def _fetch_thread_context(db, entity_id: str, card_id: str) -> str:
    """Return a short blurb situating a follow-up card within its thread.

    Costs no LLM call. Reuses the rolling ``group_summary`` already computed for
    the entity, falling back to recent sibling card headers for early follow-ups
    (group summaries only generate at card_count >= 2 and are debounced, so cards
    #2-#3 of a burst have no summary yet). Returns "" when there is nothing prior
    to situate against — the first card in a group is already self-contained.

    Only called when the entity already has sibling cards (carry-forward), so the
    DB reads are skipped entirely for brand-new entities.
    """
    # Tier 1: rolling group summary (richest synthesized thread context).
    try:
        rows = await db.execute_fetchall(
            "SELECT headline, current_status FROM group_summaries WHERE entity_id = ?",
            (entity_id,),
        )
        if rows:
            headline = (rows[0]["headline"] or "").strip()
            status = (rows[0]["current_status"] or "").strip()
            blurb = ". ".join(p for p in (headline, status) if p)
            if blurb:
                return blurb[:200]
    except Exception as e:
        # Don't fail the embed over enrichment; surface it so a persistent
        # group_summaries read problem (schema drift, corruption) is visible
        # rather than silently degrading every follow-up card's embedding.
        log.warning(
            "thread_context_group_summary_read_failed",
            entity_id=entity_id,
            error=str(e),
        )

    # Tier 2: recent prior card headers (the current card row is already inserted,
    # so exclude it). Covers the window before the group summary first generates.
    try:
        rows = await db.execute_fetchall(
            """SELECT header FROM action_cards
               WHERE entity_id = ? AND card_id != ?
               ORDER BY created_at DESC
               LIMIT 2""",
            (entity_id, card_id),
        )
        headers = [r["header"] for r in rows if r["header"]]
        if headers:
            return "; ".join(headers)[:200]
    except Exception as e:
        log.warning(
            "thread_context_header_fallback_failed",
            entity_id=entity_id,
            error=str(e),
        )

    return ""


def _resolve_card_status(worker_results: list[WorkerResult] | None) -> tuple[bool, str]:
    """Derive (has_workspace, initial_status) from worker results. A worker can
    attach a session (→ has_workspace) and override the card's initial status;
    non-worker cards default to ``ready``."""
    has_workspace = False
    card_status = "ready"
    if worker_results:
        has_workspace = any(r.session_id is not None for r in worker_results)
        for wr in worker_results:
            if wr.card_status:
                card_status = wr.card_status
    return has_workspace, card_status


async def _persist_card(
    db,
    *,
    card_id: str,
    pre_created: bool,
    event: LayaEvent,
    router_output: RouterOutput,
    stager_output: ActionCardData,
    entity_id: str,
    source_ref: str,
    source_url: str | None,
    space_id: str | None,
    has_workspace: bool,
    card_status: str,
) -> None:
    """Persist the card (UPDATE the provisional row on the worker path, INSERT a
    fresh row otherwise), enqueue it for Omni, and carry the entity group's
    activity timestamp forward — all in ONE transaction, so the new row and the
    group's bumped group_active_at become visible atomically."""
    intelligence_json = json.dumps(stager_output.intelligence_report)
    staged_output_json = json.dumps(stager_output.staged_output.model_dump())
    suggested_actions_json = json.dumps(
        [a.model_dump() for a in stager_output.suggested_actions]
    )

    if pre_created:
        # UPDATE the provisional row created by _run_workers_background.
        await db.execute(
            """UPDATE action_cards SET
               priority=?, persona=?, category=?, header=?, summary=?,
               intelligence=?, staged_output=?, suggested_actions=?,
               status=?, privacy_tier=?, has_workspace=?,
               confidence=?, entity_id=?, source_ref=?, source_url=?,
               space_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE card_id=?""",
            (
                router_output.priority.value,
                router_output.persona.value,
                router_output.category.value,
                stager_output.header,
                stager_output.summary,
                intelligence_json,
                staged_output_json,
                suggested_actions_json,
                card_status,
                stager_output.privacy_tier,
                has_workspace,
                router_output.confidence,
                entity_id,
                source_ref,
                source_url,
                space_id,
                card_id,
            ),
        )
    else:
        # created_at / group_active_at are the EVENT's time, not emit time — see
        # _event_ts. Without this, late-ingested cards read "just now".
        ev_ts = _event_ts(event)
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category, header, summary,
                intelligence, staged_output, suggested_actions, status,
                privacy_tier, has_workspace, confidence, router_model, stager_model,
                entity_id, source_ref, source_url, space_id, created_at, group_active_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id,
                event.event_id,
                router_output.priority.value,
                router_output.persona.value,
                router_output.category.value,
                stager_output.header,
                stager_output.summary,
                intelligence_json,
                staged_output_json,
                suggested_actions_json,
                card_status,
                stager_output.privacy_tier,
                has_workspace,
                router_output.confidence,
                None,  # router_model filled by audit
                None,  # stager_model filled by audit
                entity_id,
                source_ref,
                source_url,
                space_id,
                ev_ts,
                ev_ts,
            ),
        )

    # Enqueue for Omni processing (same transaction as the card persist — crash-safe).
    await db.execute(
        "INSERT OR IGNORE INTO omni_queue (card_id, space_id, created_at) VALUES (?, ?, ?)",
        (card_id, space_id, db_now()),
    )

    # Carry forward: bump group_active_at for ALL cards in this entity group to the
    # group's latest ACTIVITY time. The just-persisted card carries its own event time
    # (see _event_ts), so MAX(group_active_at) = max(existing, this event) — forward-only.
    # Using the event time (not wall-clock now) means an out-of-order/late-ingested older
    # event can never drag a group backward, and backlogged groups bucket to their real date.
    await db.execute(
        """UPDATE action_cards
           SET group_active_at = (
               SELECT MAX(group_active_at) FROM action_cards WHERE entity_id = ?
           )
           WHERE entity_id = ?""",
        (entity_id, entity_id),
    )
    await db.commit()


async def _count_siblings(db, entity_id: str, card_id: str) -> int:
    """Number of OTHER cards already in this entity group (excludes card_id).

    >0 means this card carried an existing group forward. This single count also
    gates the group summary: total cards >= 2  ⟺  siblings >= 1, so the
    carry-forward flag and the summary trigger share one query (no second COUNT)."""
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM action_cards WHERE entity_id = ? AND card_id != ?",
        (entity_id, card_id),
    )
    return rows[0]["cnt"]


async def _auto_resolve_terminal_siblings(
    db, event: LayaEvent, entity_id: str, card_id: str
) -> int:
    """A terminal event means the work item completed, so resolve this entity's
    still-active sibling cards to ``done`` — they are no longer actionable.
    Best-effort; returns the number resolved."""
    inactive = tuple(INACTIVE_STATUSES)
    placeholders = ", ".join("?" for _ in inactive)
    sibling_rows = await db.execute_fetchall(
        f"""SELECT card_id, header, status FROM action_cards
            WHERE entity_id = ? AND card_id != ?
            AND status NOT IN ({placeholders})""",
        (entity_id, card_id, *inactive),
    )
    resolved = 0
    for sib in sibling_rows:
        try:
            await transition_card_status(sib["card_id"], "done", actor="pipeline")
            resolved += 1
        except ValueError:
            continue
        create_task(
            trigger_summary_status_update(sib["card_id"], sib["header"], "done"),
            name=f"summary_status_{sib['card_id']}",
        )
    if resolved:
        log.info(
            "auto_resolved_siblings",
            entity_id=entity_id,
            trigger_event=event.source.raw_event_type,
            resolved_count=resolved,
        )
    return resolved


async def _embed_card(
    db,
    *,
    event: LayaEvent,
    router_output: RouterOutput,
    stager_output: ActionCardData,
    entity_id: str,
    card_id: str,
    is_carry_forward: bool,
    space_id: str | None,
    entity_refs: str,
) -> str:
    """Build the canonical embedding text (with an optional thread-context prefix
    for follow-up cards), persist that blurb for lexical search, and index the
    card in ChromaDB. Returns embed_text so the grouping queries reuse it verbatim
    (keeping the stored vector and the searches consistent)."""
    # Contextual embeddings: for a follow-up card joining an existing entity group,
    # prepend a short thread-context blurb so terse updates ("Approved.") keep the
    # semantic referent of the thread. Skipped for the first card (self-contained)
    # and gated by a setting so it can be disabled if grouping thresholds drift.
    ctx_embed_enabled = (
        load_settings().get("smart_grouping", {}).get("contextual_embeddings", True)
    )
    thread_context = ""
    if ctx_embed_enabled and is_carry_forward:
        try:
            thread_context = await _fetch_thread_context(db, entity_id, card_id)
        except Exception as e:
            log.warning("thread_context_fetch_failed", card_id=card_id, error=str(e))
    # Persist the blurb so the FTS5/BM25 lexical index can match on it too
    # (Contextual BM25). The cards_fts UPDATE trigger re-syncs the indexed row.
    if thread_context:
        try:
            await db.execute(
                "UPDATE action_cards SET thread_context = ? WHERE card_id = ?",
                (thread_context, card_id),
            )
            await db.commit()
        except Exception as e:
            log.warning("thread_context_persist_failed", card_id=card_id, error=str(e))
    # Canonical embedding template: structured, normalized text that maximizes
    # semantic signal for cross-platform entity linking. Each field is explicitly
    # labeled so the model weights it appropriately.
    embed_text = _build_embedding_text(
        platform=event.source.platform,
        header=stager_output.header,
        summary=stager_output.summary,
        actor_name=event.actor.name,
        subject_type=event.subject.type,
        category=router_output.category.value,
        entity_refs=entity_refs,
        thread_context=thread_context,
    )
    # Build tags CSV for ChromaDB metadata (tags NOT in embedding text — preserves semantic space)
    from laya.pipeline.tags import get_tags_csv
    try:
        tags_csv = await get_tags_csv(card_id)
    except Exception:
        tags_csv = ""

    try:
        await embed_document(
            doc_id=card_id,
            text=embed_text,
            metadata={
                "content_type": "card_summary",
                "card_id": card_id,
                "source_event_id": event.event_id,
                "source_platform": event.source.platform,
                # The canonical grouping key — trace seeds need this for dedup and
                # feedback exclusion; previously only entity_refs (a CSV) was
                # stored and trace mis-used it as entity_id (review §2 — P4-4).
                "entity_id": entity_id,
                "entity_refs": entity_refs,
                "persona": router_output.persona.value,
                "priority": router_output.priority.value,
                "timestamp": event.timestamp.timestamp(),
                "space_id": space_id or "",
                "tags": tags_csv,
            },
        )
    except Exception as e:
        log.warning("card_embed_failed", card_id=card_id, error=str(e))
    return embed_text


async def _resolve_context_grouping(
    db,
    *,
    event: LayaEvent,
    stager_output: ActionCardData,
    entity_id: str,
    card_id: str,
    embed_text: str,
    space_id: str | None,
    entity_refs: str,
) -> str | None:
    """Find related cards across entity boundaries and assign a context group.
    Prefers a cross-entity match the stager already identified (saves an LLM call),
    falling back to a post-emit ChromaDB search for races. Persists and returns the
    assigned context_id, or None."""
    sg_config = load_settings().get("smart_grouping", {})
    if not sg_config.get("context_association", True):
        return None

    context_id = None
    stager_context_match = getattr(stager_output, "context_match", None)
    if stager_context_match is None and isinstance(stager_output, dict):
        stager_context_match = stager_output.get("context_match")

    if stager_context_match and stager_context_match.get("matched_card_id"):
        # Stager identified a cross-entity context match — validate and use it.
        from laya.pipeline.context_grouping import assign_or_join_context_group
        matched_card_id = stager_context_match["matched_card_id"]
        label = stager_context_match.get("label", "")
        try:
            match_rows = await db.execute_fetchall(
                "SELECT entity_id, context_id FROM action_cards WHERE card_id = ?",
                (matched_card_id,),
            )
            if match_rows and match_rows[0]["entity_id"] != entity_id:
                # Fetch matched card's entity_refs from ChromaDB metadata
                matched_refs = ""
                try:
                    from laya.db.chromadb_store import get_collection as _get_col
                    from functools import partial as _partial
                    _col = _get_col()
                    _loop = asyncio.get_event_loop()
                    _meta_result = await _loop.run_in_executor(
                        None, _partial(_col.get, ids=[matched_card_id], include=["metadatas"])
                    )
                    if _meta_result and _meta_result.get("metadatas"):
                        matched_refs = _meta_result["metadatas"][0].get("entity_refs", "")
                except Exception:
                    pass

                context_id = await assign_or_join_context_group(
                    card_id, matched_card_id, label, space_id,
                    entity_refs=entity_refs,
                    matched_entity_refs=matched_refs,
                )
                if context_id:
                    log.info(
                        "context_group_from_stager",
                        card_id=card_id,
                        matched_card_id=matched_card_id,
                        context_id=context_id,
                    )
        except Exception as e:
            log.warning("stager_context_match_failed", card_id=card_id, error=str(e))

    if not context_id:
        # Fallback: post-emit ChromaDB search (handles race conditions).
        from laya.pipeline.context_grouping import resolve_context_group
        try:
            context_id = await resolve_context_group(
                card_id=card_id,
                entity_id=entity_id,
                embed_text=embed_text,
                space_id=space_id,
                platform=event.source.platform,
                entity_refs=entity_refs,
            )
        except Exception as e:
            log.warning("context_grouping_failed", card_id=card_id, error=str(e))

    if context_id:
        await db.execute(
            "UPDATE action_cards SET context_id = ? WHERE card_id = ?",
            (context_id, card_id),
        )
        await db.commit()
        log.info("context_group_assigned", card_id=card_id, context_id=context_id)
    return context_id


async def _broadcast_card(
    *,
    pre_created: bool,
    card_id: str,
    entity_id: str,
    stager_output: ActionCardData,
    router_output: RouterOutput,
    card_status: str,
    has_workspace: bool,
    is_carry_forward: bool,
) -> None:
    """Broadcast the new/updated card to the feed, plus a carry-forward notice."""
    # Pre-created cards (agent_running → pending transition) use card_updated so the
    # feed patches the existing card in-place rather than triggering a full reload.
    ws_type = "card_updated" if pre_created else "card_created"
    await manager.broadcast(
        {
            "type": ws_type,
            "card_id": card_id,
            "payload": {
                "header": stager_output.header,
                "summary": stager_output.summary,
                "priority": router_output.priority.value,
                "persona": router_output.persona.value,
                "category": router_output.category.value,
                "status": card_status,
                "has_workspace": has_workspace,
                "privacy_tier": stager_output.privacy_tier,
                "suggested_tags": stager_output.suggested_tags,
            },
        }
    )
    # If this card carried forward an existing group, notify the UI.
    if is_carry_forward:
        await manager.broadcast(
            {
                "type": "group_carried_forward",
                "card_id": card_id,
                "payload": {"entity_id": entity_id},
            }
        )


def _trigger_followups(
    *,
    event: LayaEvent,
    router_output: RouterOutput,
    stager_output: ActionCardData,
    card_id: str,
    entity_id: str,
    space_id: str | None,
    is_carry_forward: bool,
) -> None:
    """Kick off the non-blocking post-emit work: rolling group summary (only for
    multi-card groups — i.e. carry-forward), the daily summary update, and the
    bounded processing-rules evaluation. Omni needs no trigger here: the card was
    enqueued atomically in _persist_card and the omni queue processor picks it up."""
    # A group summary only makes sense at >= 2 cards, which is exactly carry-forward.
    if is_carry_forward:
        from laya.pipeline.group_summary import trigger_group_summary_update
        create_task(
            trigger_group_summary_update(entity_id, card_id, space_id),
            name=f"group_summary_{entity_id}",
        )

    # Daily summary (space metadata is hydrated inside summarize.py via a DB join).
    create_task(
        trigger_summary_update(
            card_id=card_id,
            card_header=stager_output.header,
            card_summary=stager_output.summary,
            card_priority=router_output.priority.value,
            card_category=router_output.category.value,
            space_id=space_id,
            card_persona=router_output.persona.value,
            card_intelligence=stager_output.intelligence_report,
            actor_name=event.actor.name,
            source_platform=event.source.platform,
            card_tags=stager_output.suggested_tags,
        ),
        name=f"summary_{card_id}",
    )

    # Processing rules — evaluate automated actions (non-blocking, bounded).
    from laya.pipeline.processing_rules import run_processing_rules, _processing_semaphore

    async def _bounded_processing_rules():
        async with _processing_semaphore:
            await run_processing_rules(
                event=event,
                router_output=router_output,
                card_id=card_id,
                entity_id=entity_id,
                space_id=space_id,
                is_carry_forward=is_carry_forward,
            )

    create_task(
        _bounded_processing_rules(),
        name=f"processing_rules_{card_id}",
    )


async def run_emit(
    event: LayaEvent,
    router_output: RouterOutput,
    stager_output: ActionCardData,
    worker_results: list[WorkerResult] | None = None,
    card_id: str | None = None,
    space_id: str | None = None,
) -> str:
    """Run the EMIT step: persist card → embed → resolve entities → audit → broadcast.

    Args:
        event: The original event.
        router_output: Router classification.
        stager_output: Stager-generated card data.
        worker_results: Optional worker findings (for has_workspace detection).
        card_id: Pre-generated card_id from the worker background flow. When
            provided the provisional card row is updated in-place; when None a
            fresh row is inserted.
        space_id: The resolved space for this event.

    Returns:
        card_id of the created/updated card.
    """
    pre_created = card_id is not None
    if not pre_created:
        card_id = f"card_{uuid.uuid4().hex[:12]}"
    # Canonical grouping key — the one true spelling, shared with the worker path
    # (queue.py) and the stager's history lookup (stager.py) so all three agree.
    # Any per-platform shape correction (e.g. resolving Gmail's canonical thread
    # root when the trigger returns a message id as the threadId) is the n8n
    # ingestion workflow's job, NOT emit's — emit is platform-agnostic.
    entity_id = event.entity_id

    # Build a human-readable source reference for linking back to the origin
    from laya.egress.registry import format_source_ref

    source_url = event.subject.url or None
    source_ref, source_url = format_source_ref(
        event.source.platform,
        event.subject.id,
        event.subject.type,
        event.subject.title,
        source_url,
    )

    has_workspace, card_status = _resolve_card_status(worker_results)

    # 1. Persist the card + enqueue it for omni + carry the group forward (one txn).
    db = await get_db()
    await _persist_card(
        db,
        card_id=card_id,
        pre_created=pre_created,
        event=event,
        router_output=router_output,
        stager_output=stager_output,
        entity_id=entity_id,
        source_ref=source_ref,
        source_url=source_url,
        space_id=space_id,
        has_workspace=has_workspace,
        card_status=card_status,
    )

    # Persist stager-suggested tags (best-effort).
    if stager_output.suggested_tags:
        from laya.pipeline.tags import persist_suggested_tags
        try:
            await persist_suggested_tags(card_id, stager_output.suggested_tags)
        except Exception as e:
            log.warning("tag_persist_failed", card_id=card_id, error=str(e))

    # 2. Carry-forward detection — one query, also gates the group summary below.
    is_carry_forward = await _count_siblings(db, entity_id, card_id) > 0
    log.info(
        "card_created",
        card_id=card_id,
        event_id=event.event_id,
        priority=router_output.priority.value,
        persona=router_output.persona.value,
        carry_forward=is_carry_forward,
    )

    # 3. Auto-resolve sibling cards when a terminal event arrives (best-effort).
    # Which event types are terminal is a per-platform concern owned by the egress
    # platform registry — emit just asks the event (see LayaEvent.is_terminal).
    if is_carry_forward and event.is_terminal:
        try:
            await _auto_resolve_terminal_siblings(db, event, entity_id, card_id)
        except Exception as e:
            log.warning("auto_resolve_siblings_failed", card_id=card_id, error=str(e))

    # 4. Embed in ChromaDB; embed_text is reused verbatim by the grouping queries.
    entity_refs = ",".join(e.value for e in router_output.entities)
    embed_text = await _embed_card(
        db,
        event=event,
        router_output=router_output,
        stager_output=stager_output,
        entity_id=entity_id,
        card_id=card_id,
        is_carry_forward=is_carry_forward,
        space_id=space_id,
        entity_refs=entity_refs,
    )

    # 5. Semantic context grouping across entity boundaries (best-effort).
    await _resolve_context_grouping(
        db,
        event=event,
        stager_output=stager_output,
        entity_id=entity_id,
        card_id=card_id,
        embed_text=embed_text,
        space_id=space_id,
        entity_refs=entity_refs,
    )

    # 6. Entity resolution Layer 2 (semantic, non-blocking).
    entity_values = [e.value for e in router_output.entities]
    if entity_values:
        try:
            await resolve_semantic_entities(card_id, embed_text, entity_values)
        except Exception as e:
            log.warning("entity_resolution_failed", card_id=card_id, error=str(e))

    # 7. Audit log.
    await log_to_audit(
        event_id=event.event_id,
        card_id=card_id,
        step="emit",
        model="n/a",
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        success=True,
        metadata={"has_workspace": has_workspace, "privacy_tier": stager_output.privacy_tier},
    )

    # 8. Broadcast to the feed (runs even if the enrichment steps above degraded).
    await _broadcast_card(
        pre_created=pre_created,
        card_id=card_id,
        entity_id=entity_id,
        stager_output=stager_output,
        router_output=router_output,
        card_status=card_status,
        has_workspace=has_workspace,
        is_carry_forward=is_carry_forward,
    )

    # 9. Kick off non-blocking follow-ups (group summary, daily summary, rules).
    _trigger_followups(
        event=event,
        router_output=router_output,
        stager_output=stager_output,
        card_id=card_id,
        entity_id=entity_id,
        space_id=space_id,
        is_carry_forward=is_carry_forward,
    )

    return card_id
