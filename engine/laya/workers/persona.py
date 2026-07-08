# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Spec-driven LLM-drafting persona worker.

COMMS / OPS / SALES / HR / FINANCE were five near-verbatim copies that differed
only in prompt builder, JSON schema, memory breadth (n_results), whether they
pass role-aware context to their prompt builder, and their result / fallback
shape — and they had already drifted (ops/finance silently dropped the role-aware
kwargs; the parsed-findings shape diverged). One ``run_persona_worker`` driven by
a ``PersonaSpec`` table replaces them, so each difference is now explicit config
rather than copy-paste (review §5.1 — P7-2).

ENGINEER stays in its own module: it drives a coding agent / research flow and
shares none of this drafting shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import structlog

from laya.pipeline.related_context import query_related_context
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.comms import build_comms_messages, get_comms_json_schema
from laya.llm.prompts.finance import build_finance_messages, get_finance_json_schema
from laya.llm.prompts.hr import build_hr_messages, get_hr_json_schema
from laya.llm.prompts.ops import build_ops_messages, get_ops_json_schema
from laya.llm.prompts.sales import build_sales_messages, get_sales_json_schema
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.workers.base import WorkerResult

log = structlog.get_logger()


@dataclass(frozen=True)
class PersonaSpec:
    """Everything that made one drafting worker differ from another."""

    persona: str
    build_messages: Callable
    get_schema: Callable
    n_results: int = 3
    # Reply-style workers (comms/hr/sales) pass the role-aware kwargs to their
    # prompt builder; synthesis-style workers (ops/finance) don't — their builders
    # don't accept them. An explicit flag surfaces what used to be silent drift.
    role_aware: bool = True
    # Parsed findings shape: reply workers wrap in {"draft": ...}; synthesis
    # workers use the parsed object directly.
    wrap_draft: bool = True
    # Static fields of the fallback drafted_output when the LLM returned no valid
    # JSON (draft_reply is filled from the raw content). None ⇒ no fallback
    # drafted_output at all (ops/finance).
    fallback_output: dict[str, Any] | None = None


# The single source of truth for the five drafting personas. Adding one is now a
# table row, not a new ~70-line file.
PERSONA_SPECS: dict[str, PersonaSpec] = {
    "COMMS": PersonaSpec(
        "COMMS", build_comms_messages, get_comms_json_schema,
        n_results=3, role_aware=True, wrap_draft=True,
        fallback_output={"tone": "professional", "reasoning": ""},
    ),
    "HR": PersonaSpec(
        "HR", build_hr_messages, get_hr_json_schema,
        n_results=3, role_aware=True, wrap_draft=True,
        fallback_output={"tone": "professional", "sensitivity_note": "none", "reasoning": ""},
    ),
    "SALES": PersonaSpec(
        "SALES", build_sales_messages, get_sales_json_schema,
        n_results=3, role_aware=True, wrap_draft=True,
        fallback_output={"tone": "professional", "account_context": "", "reasoning": ""},
    ),
    "OPS": PersonaSpec(
        "OPS", build_ops_messages, get_ops_json_schema,
        n_results=5, role_aware=False, wrap_draft=False, fallback_output=None,
    ),
    "FINANCE": PersonaSpec(
        "FINANCE", build_finance_messages, get_finance_json_schema,
        n_results=5, role_aware=False, wrap_draft=False, fallback_output=None,
    ),
}


async def run_persona_worker(
    spec: PersonaSpec,
    event: LayaEvent,
    router_output: RouterOutput,
    prior_findings: dict | None = None,
    card_id: str | None = None,
    user_identity: dict | None = None,
    actor_relationship: str = "external",
    participant_roles: dict | None = None,
) -> WorkerResult:
    """Run one LLM-drafting persona worker (no coding agent — pure LLM drafting)."""
    log.info("persona_worker_start", persona=spec.persona, event_id=event.event_id)

    # Shared per-event memoized search — reuses the router/stager embedding (P6-7).
    related_context = await query_related_context(event, n_results=spec.n_results)

    if spec.role_aware:
        messages = spec.build_messages(
            event, router_output, related_context, prior_findings,
            user_identity=user_identity,
            actor_relationship=actor_relationship,
            participant_roles=participant_roles,
        )
    else:
        messages = spec.build_messages(event, router_output, related_context)

    response = await llm_call(
        role="stager",
        messages=messages,
        response_schema=spec.get_schema(),
        event_id=event.event_id,
        card_id=card_id,
        step="worker",
        temperature=0.3,
        max_tokens=DEFAULT_MAX_TOKENS,
    )

    if response.parsed:
        findings = {"draft": response.parsed} if spec.wrap_draft else response.parsed
        return WorkerResult(persona=spec.persona, findings=findings, drafted_output=response.parsed)

    # No valid JSON — degrade to the raw text, with the persona's fallback shape.
    if spec.fallback_output is not None:
        return WorkerResult(
            persona=spec.persona,
            findings={"raw_response": response.content},
            drafted_output={"draft_reply": response.content, **spec.fallback_output},
        )
    return WorkerResult(persona=spec.persona, findings={"raw_response": response.content})
