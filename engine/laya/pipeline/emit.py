"""EMIT pipeline step — persist action card to SQLite, ChromaDB, and broadcast."""

import json
import uuid

import structlog

from laya.api.websocket import manager
from laya.db.chromadb_store import embed_document
from laya.db.sqlite import get_db
from laya.llm.client import _log_to_audit
from laya.models.card import ActionCardData
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.pipeline.entity_resolution import resolve_semantic_entities
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def run_emit(
    event: LayaEvent,
    router_output: RouterOutput,
    stager_output: ActionCardData,
    worker_results: list[WorkerResult] | None = None,
) -> str:
    """Run the EMIT step: persist card → embed → resolve entities → audit → broadcast.

    Args:
        event: The original event.
        router_output: Router classification.
        stager_output: Stager-generated card data.
        worker_results: Optional worker findings (for has_workspace detection).

    Returns:
        card_id of the created card.
    """
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    entity_id = f"{event.source.platform}:{event.subject.type}:{event.subject.id}"

    # 1. Detect has_workspace
    has_workspace = False
    if worker_results:
        has_workspace = any(r.session_id is not None for r in worker_results)

    # 2. Serialize JSON fields
    intelligence_json = json.dumps(stager_output.intelligence_report)
    staged_output_json = json.dumps(stager_output.staged_output.model_dump())
    suggested_actions_json = json.dumps(
        [a.model_dump() for a in stager_output.suggested_actions]
    )

    # 3. Insert into action_cards
    db = await get_db()
    await db.execute(
        """INSERT INTO action_cards
           (card_id, event_id, priority, persona, category, header, summary,
            intelligence, staged_output, suggested_actions, status,
            privacy_tier, has_workspace, confidence, router_model, stager_model,
            entity_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            "pending",
            stager_output.privacy_tier,
            has_workspace,
            router_output.confidence,
            None,  # router_model filled by audit
            None,  # stager_model filled by audit
            entity_id,
        ),
    )
    await db.commit()

    log.info(
        "card_created",
        card_id=card_id,
        event_id=event.event_id,
        priority=router_output.priority.value,
        persona=router_output.persona.value,
    )

    # 4. Embed card in ChromaDB
    embed_text = f"{stager_output.header}\n{stager_output.summary}"
    entity_refs = ",".join(e.value for e in router_output.entities)
    try:
        await embed_document(
            doc_id=f"card_{card_id}",
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

    # 7. Broadcast card_created via WebSocket
    await manager.broadcast(
        {
            "type": "card_created",
            "card_id": card_id,
            "payload": {
                "header": stager_output.header,
                "summary": stager_output.summary,
                "priority": router_output.priority.value,
                "persona": router_output.persona.value,
                "category": router_output.category.value,
                "status": "pending",
                "has_workspace": has_workspace,
                "privacy_tier": stager_output.privacy_tier,
            },
        }
    )

    return card_id
