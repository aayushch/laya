# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""OPS Worker — calendar prep briefings and operational synthesis."""

from __future__ import annotations

import structlog

from laya.db.chromadb_store import memory_search
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.ops import build_ops_messages, get_ops_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def run_ops(
    event: LayaEvent,
    router_output: RouterOutput,
    card_id: str | None = None,
) -> WorkerResult:
    """Run the OPS worker: generate briefings and meeting prep.

    No coding agent needed — LLM synthesis from event history + context.
    """
    log.info("ops_worker_start", event_id=event.event_id)

    # Gather context
    query = f"{event.subject.title} {event.content.body[:300]}"
    related_context: list[dict] = []
    try:
        related_context = await memory_search(query, n_results=5)
    except Exception as e:
        log.warning("ops_context_search_failed", error=str(e))

    messages = build_ops_messages(event, router_output, related_context)
    schema = get_ops_json_schema()

    response = await llm_call(
        role="stager",
        messages=messages,
        response_schema=schema,
        event_id=event.event_id,
        card_id=card_id,
        step="worker",
        temperature=0.3,
        max_tokens=DEFAULT_MAX_TOKENS,
    )

    if response.parsed:
        return WorkerResult(
            persona="OPS",
            findings=response.parsed,
            drafted_output=response.parsed,
        )

    return WorkerResult(
        persona="OPS",
        findings={"raw_response": response.content},
    )
