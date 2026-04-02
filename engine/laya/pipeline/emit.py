"""EMIT pipeline step — persist action card to SQLite, ChromaDB, and broadcast."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import structlog

from laya.api.websocket import manager
from laya.db.chromadb_store import embed_document
from laya.db.sqlite import get_db
from laya.llm.client import _log_to_audit
from laya.models.card import ActionCardData
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.entity_resolution import resolve_semantic_entities
from laya.pipeline.summarize import trigger_summary_update
from laya.workers.base import WorkerResult

log = structlog.get_logger()


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
    entity_id = f"{event.source.platform}:{event.subject.type}:{event.subject.id}"

    # For Gmail: normalize entity_id to the canonical thread ID so all messages
    # in the same thread group together.  The n8n trigger sometimes returns the
    # message ID as threadId, so we look for an existing event whose subject_id
    # (the real thread root) matches the gmail_thread_id in *our* metadata, or
    # vice-versa.
    if event.source.platform == "gmail":
        gmail_thread_id = (event.content.metadata or {}).get("gmail_thread_id")
        if gmail_thread_id and gmail_thread_id != event.subject.id:
            # Our threadId differs from subject.id — check if the real thread
            # root already exists as another event's subject_id
            db_tmp = await get_db()
            existing = await db_tmp.execute_fetchall(
                """SELECT entity_id FROM action_cards
                   WHERE entity_id = ?
                   LIMIT 1""",
                (f"gmail:email_thread:{gmail_thread_id}",),
            )
            if existing:
                entity_id = existing[0]["entity_id"]
                log.debug("gmail_entity_resolved_via_metadata", entity_id=entity_id)
        if entity_id == f"gmail:email_thread:{event.subject.id}":
            # Still not resolved — check if any existing event's metadata
            # references our subject.id as its gmail_thread_id
            db_tmp = await get_db()
            existing = await db_tmp.execute_fetchall(
                """SELECT ac.entity_id FROM action_cards ac
                   JOIN events e ON e.event_id = ac.event_id
                   WHERE ac.entity_id LIKE 'gmail:email_thread:%'
                     AND e.content_metadata LIKE ?
                   LIMIT 1""",
                (f'%"gmail_thread_id": "{event.subject.id}"%',),
            )
            if existing:
                entity_id = existing[0]["entity_id"]
                log.debug("gmail_entity_resolved_via_reverse_lookup", entity_id=entity_id)

    # Build a human-readable source reference for linking back to the origin
    source_url = event.subject.url or None
    _platform = event.source.platform
    _subj_id = event.subject.id
    if _platform == "github":
        source_ref = f"PR #{_subj_id}" if event.subject.type == "pull_request" else f"#{_subj_id}"
    elif _platform == "jira":
        source_ref = _subj_id  # already "PROJ-123"
    elif _platform == "gmail":
        source_ref = event.subject.title or _subj_id
    elif _platform == "slack":
        source_ref = event.subject.title or _subj_id
    else:
        source_ref = _subj_id or None

    # 1. Detect has_workspace and resolve card status from worker results
    has_workspace = False
    card_status = "ready"  # default for non-worker cards
    if worker_results:
        has_workspace = any(r.session_id is not None for r in worker_results)
        # Worker can override the initial card status
        for wr in worker_results:
            if wr.card_status:
                card_status = wr.card_status

    # 2. Serialize JSON fields
    intelligence_json = json.dumps(stager_output.intelligence_report)
    staged_output_json = json.dumps(stager_output.staged_output.model_dump())
    suggested_actions_json = json.dumps(
        [a.model_dump() for a in stager_output.suggested_actions]
    )

    # 3. Persist card — UPDATE the provisional row when card was pre-created by
    #    _run_workers_background, INSERT fresh for the simple (no-worker) path.
    db = await get_db()
    if pre_created:
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
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category, header, summary,
                intelligence, staged_output, suggested_actions, status,
                privacy_tier, has_workspace, confidence, router_model, stager_model,
                entity_id, source_ref, source_url, space_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
    await db.commit()

    # Carry forward: update group_active_at for ALL cards in this entity group
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "UPDATE action_cards SET group_active_at = ? WHERE entity_id = ?",
        (now_ts, entity_id),
    )
    await db.commit()

    # Detect if this card joined an existing entity group (carry-forward scenario)
    _existing = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM action_cards WHERE entity_id = ? AND card_id != ?",
        (entity_id, card_id),
    )
    _is_carry_forward = _existing[0]["cnt"] > 0

    log.info(
        "card_created",
        card_id=card_id,
        event_id=event.event_id,
        priority=router_output.priority.value,
        persona=router_output.persona.value,
        carry_forward=_is_carry_forward,
    )

    # 4. Embed card in ChromaDB
    entity_refs = ",".join(e.value for e in router_output.entities)
    # Build prose-like text for richer, more distinctive embeddings
    embed_parts = [f"{stager_output.header}. {stager_output.summary}"]
    if entity_refs:
        embed_parts.append(f"Related entities: {entity_refs}.")
    if router_output.category:
        embed_parts.append(f"Category: {router_output.category.value}.")
    embed_parts.append(f"Source: {event.source.platform}.")
    embed_text = "\n".join(embed_parts)
    try:
        await embed_document(
            doc_id=card_id,
            text=embed_text,
            metadata={
                "content_type": "card_summary",
                "card_id": card_id,
                "source_event_id": event.event_id,
                "source_platform": event.source.platform,
                "entity_refs": entity_refs,
                "persona": router_output.persona.value,
                "priority": router_output.priority.value,
                "timestamp": event.timestamp.isoformat(),
                "space_id": space_id or "",
            },
        )
    except Exception as e:
        log.warning("card_embed_failed", card_id=card_id, error=str(e))

    # 5. Entity resolution Layer 2 (semantic, non-blocking)
    entity_values = [e.value for e in router_output.entities]
    if entity_values:
        try:
            await resolve_semantic_entities(card_id, embed_text, entity_values)
        except Exception as e:
            log.warning("entity_resolution_failed", card_id=card_id, error=str(e))

    # 6. Audit log
    await _log_to_audit(
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

    # 7. Broadcast via WebSocket.
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
            },
        }
    )

    # 7b. If this card carried forward an existing group, notify the UI
    if _is_carry_forward:
        await manager.broadcast(
            {
                "type": "group_carried_forward",
                "card_id": card_id,
                "payload": {"entity_id": entity_id},
            }
        )

    # 8. Trigger daily summary update (async, non-blocking)
    # Resolve space name/color for summary display
    _space_name: str | None = None
    _space_color: str | None = None
    if space_id:
        _space_row = await db.execute_fetchall(
            "SELECT name, color FROM spaces WHERE space_id = ?", (space_id,)
        )
        if _space_row:
            _space_name = _space_row[0]["name"]
            _space_color = _space_row[0]["color"]

    asyncio.create_task(
        trigger_summary_update(
            card_id=card_id,
            card_header=stager_output.header,
            card_summary=stager_output.summary,
            card_priority=router_output.priority.value,
            card_category=router_output.category.value,
            card_persona=router_output.persona.value,
            card_intelligence=stager_output.intelligence_report,
            actor_name=event.actor.name,
            source_platform=event.source.platform,
            space_id=space_id,
            space_name=_space_name,
            space_color=_space_color,
        ),
        name=f"summary_{card_id}",
    )

    return card_id
