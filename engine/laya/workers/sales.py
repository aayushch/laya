# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""SALES Worker — draft customer/prospect communications using LLM + memory context."""

from __future__ import annotations

import structlog

from laya.db.chromadb_store import memory_search
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.sales import build_sales_messages, get_sales_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

log = structlog.get_logger()


async def run_sales(
    event: LayaEvent,
    router_output: RouterOutput,
    prior_findings: dict | None = None,
    card_id: str | None = None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
) -> WorkerResult:
    """Run the SALES worker: draft a customer/prospect reply with account context."""
    log.info("sales_worker_start", event_id=event.event_id)

    query = f"{event.subject.title} {event.content.body[:300]}"
    related_context: list[dict] = []
    try:
        related_context = await memory_search(query, n_results=3)
    except Exception as e:
        log.warning("sales_context_search_failed", error=str(e))

    messages = build_sales_messages(
        event,
        router_output,
        related_context,
        prior_findings,
        user_identity=user_identity,
        actor_relationship=actor_relationship,
        participant_roles=participant_roles,
    )
    schema = get_sales_json_schema()

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
            persona="SALES",
            findings={"draft": response.parsed},
            drafted_output=response.parsed,
        )

    return WorkerResult(
        persona="SALES",
        findings={"raw_response": response.content},
        drafted_output={
            "draft_reply": response.content,
            "tone": "professional",
            "account_context": "",
            "reasoning": "",
        },
    )
