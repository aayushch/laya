"""FINANCE Worker — synthesize financial briefings from event content + memory context."""

from __future__ import annotations

import structlog

from laya.db.chromadb_store import memory_search
from laya.llm.client import llm_call
from laya.llm.prompts.finance import build_finance_messages, get_finance_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def run_finance(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
) -> WorkerResult:
    """Run the FINANCE worker: produce a financial briefing with key figures and open items."""
    log.info("finance_worker_start", event_id=event.event_id)

    query = f"{event.subject.title} {event.content.body[:300]}"
    related_context: list[dict] = []
    try:
        related_context = await memory_search(query, n_results=5)
    except Exception as e:
        log.warning("finance_context_search_failed", error=str(e))

    messages = build_finance_messages(event, router_output, related_context)
    schema = get_finance_json_schema()

    response = await llm_call(
        role="stager",
        messages=messages,
        response_schema=schema,
        event_id=event.event_id,
        card_id=card_id,
        step="worker",
        temperature=0.3,
        max_tokens=1500,
    )

    if response.parsed:
        return WorkerResult(
            persona="FINANCE",
            findings=response.parsed,
            drafted_output=response.parsed,
        )

    return WorkerResult(
        persona="FINANCE",
        findings={"raw_response": response.content},
    )
