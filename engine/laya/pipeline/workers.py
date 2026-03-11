"""Worker orchestration — dispatch and run workers based on Router output."""

from __future__ import annotations

import structlog

from laya.models.classification import Persona, RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult
from laya.workers.comms import run_comms
from laya.workers.engineer import run_engineer
from laya.workers.ops import run_ops

log = structlog.get_logger()


async def run_workers(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
    space_id: str | None = None,
) -> list[WorkerResult]:
    """Dispatch workers based on Router output.

    Runs primary persona worker first. If secondary_persona is set,
    runs the second worker sequentially, passing primary findings forward.
    """
    results: list[WorkerResult] = []

    # Primary worker
    primary_result = await _dispatch_worker(
        persona=router_output.persona,
        event=event,
        router_output=router_output,
        card_id=card_id,
        space_id=space_id,
    )
    results.append(primary_result)

    # Secondary worker (if specified)
    if router_output.secondary_persona:
        secondary_result = await _dispatch_worker(
            persona=router_output.secondary_persona,
            event=event,
            router_output=router_output,
            card_id=card_id,
            prior_findings=primary_result.findings,
            space_id=space_id,
        )
        results.append(secondary_result)

    log.info(
        "workers_complete",
        event_id=event.event_id,
        worker_count=len(results),
        personas=[r.persona for r in results],
    )
    return results


async def _dispatch_worker(
    persona: Persona,
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
    prior_findings: dict | None = None,
    space_id: str | None = None,
) -> WorkerResult:
    """Dispatch to the appropriate worker based on persona."""
    try:
        match persona:
            case Persona.ENGINEER:
                return await run_engineer(event, router_output, card_id=card_id, space_id=space_id)
            case Persona.COMMS:
                return await run_comms(
                    event, router_output, prior_findings=prior_findings, card_id=card_id
                )
            case Persona.OPS:
                return await run_ops(event, router_output, card_id=card_id)
            case _:
                log.warning("unknown_persona", persona=persona.value)
                return WorkerResult(persona=persona.value, error=f"Unknown persona: {persona.value}")
    except Exception as e:
        log.error("worker_dispatch_failed", persona=persona.value, error=str(e))
        return WorkerResult(persona=persona.value, error=str(e))
