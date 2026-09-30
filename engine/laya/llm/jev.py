# SPDX-License-Identifier: Apache-2.0

"""Jev fast path for the ROUTER step.

Jev (TypeSafe, served through OpenRouter's /api/alpha/decisions) does not generate
text: it answers closed questions about a `state` with probabilities, in well under a
second and for ~$0.00005 a call (measured with Laya's four questions). That covers the router's classification fields
(category, persona, priority, requires_research) but not entities, research_plan or
reasoning, so the router asks Jev first and falls back to the normal LLM whenever Jev
is unsure, the event needs research, or the call fails. `classify()` returning None
always means "use the LLM" — Jev can make routing cheaper, never break it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import structlog

from laya.config import load_settings
from laya.models.classification import ExtractedEntity, RouterOutput
from laya.models.event import LayaEvent

log = structlog.get_logger()

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_JEV_CONFIG = {"enabled": False, "model": "typesafe/jev-1.13", "threshold": 0.7}

# Prefixes whose models run in someone else's cloud. Anything else (ollama/, a custom
# provider id) is treated as local.
# ponytail: prefix heuristic; a custom provider id pointing at a cloud gateway counts as
# local here, which only makes Jev skip more private events, never fewer.
_CLOUD_PREFIXES = ("anthropic/", "openai/", "gemini/", "openrouter/", "agent/")

# Descriptions mirror the router system prompt (llm/prompts/router.py) so Jev and the
# LLM classify by the same rules. Jev reads English best; keys are the enum values.
_QUESTIONS: dict[str, dict[str, Any]] = {
    "category": {
        "type": "choice",
        "instructions": "Which category best fits the work event described by `platform`, `event_type`, `subject` and `body`?",
        "criteria": {
            "CODE": "Code-related: bugs, pull requests, code changes, builds, deploys, technical systems.",
            "COMMS": "Communication: messages, replies, mentions and threads not mainly about code, people matters or money.",
            "PEOPLE": "Team and people: hiring, candidates, interviews, onboarding, time off, performance, personnel.",
            "FINANCE": "Money: invoices, expenses, payments, budgets, forecasts, revenue, vendor contracts.",
            "OPS": "Operations and logistics: scheduling, meetings, calendar, status updates, admin, processes.",
        },
    },
    "persona": {
        "type": "choice",
        "instructions": (
            "Which assistant should handle the work event in `subject` and `body`? If it needs reading, "
            "writing or reviewing code, or investigating technical systems, pick ENGINEER even when a "
            "non-technical person reported it. Follow `user_rules` when they apply."
        ),
        "criteria": {
            "ENGINEER": "Code or technical work: bugs, PR review, code changes, build or deploy issues, technical investigations.",
            "COMMS": "Generic internal communication: teammate or manager messages, replies, mentions, threads that are not about customers, hiring or money.",
            "OPS": "Scheduling and logistics: calendar and meeting prep, operational status, non-financial internal coordination.",
            "SALES": "Prospects, customers or deals: inbound leads, customer-facing threads, CRM updates, quotes, renewals, churn.",
            "HR": "People lifecycle: hiring pipeline, candidates, interview feedback, onboarding, PTO, benefits, performance cycles.",
            "FINANCE": "Money and budgets: expenses, invoices, purchase approvals, forecasts, revenue reports, vendor contracts.",
        },
    },
    "priority": {
        "type": "choice",
        "instructions": (
            "How urgently must the user look at the work event in `subject` and `body`? Consider "
            "`actor_relationship`: a manager's direct request is more urgent, an external person slightly "
            "more, bots are usually LOW, and 'self' means the user did it themselves (usually LOW). "
            "Follow `user_rules` when they apply."
        ),
        "criteria": {
            "LOW": "Informational: bot notifications, status changes, approvals, merges, FYI updates. No action needed.",
            "MEDIUM": "Needs attention today: general tickets, non-urgent mentions, FYI emails that may need a reply.",
            "HIGH": "Needs attention soon: bug reports, code review requests, direct messages waiting for a response.",
            "CRITICAL": "Needs immediate attention: production incidents, security alerts, blocking bugs, urgent requests from a manager.",
        },
    },
    "research": {
        "type": "noul",
        "instructions": (
            "Does the work event in `subject` and `body` need investigation before anyone can act on it "
            "(a bug report, a code review request, a complex question)?"
        ),
        "criteria": {
            "true": "It needs investigation: reading code, logs, tickets or past context before replying or acting.",
            "false": "It is a simple notification, status change, approval, acknowledgement or easy reply.",
        },
    },
}

# ponytail: regex entity extraction for the Jev path only (ticket keys, PR numbers,
# @mentions). The LLM router also finds file paths, repos and branches; add those here
# if context grouping misses links on Jev-classified cards.
_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
_PR_RE = re.compile(r"\b(?:PR|MR|pull request)\s*#?\s*(\d{1,6})\b", re.IGNORECASE)
# Handles may contain "." and "-" but never end with them (sentence punctuation).
_MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9_.-]{0,37}[A-Za-z0-9_])")
_MAX_ENTITIES = 10


def get_jev_config() -> dict:
    return {**DEFAULT_JEV_CONFIG, **(load_settings().get("router_jev") or {})}


def _api_key() -> str | None:
    from laya.security.keychain import get_api_key

    return get_api_key("openrouter") or os.getenv("OPENROUTER_API_KEY")


def is_private_source(event: LayaEvent) -> bool:
    """True if the event comes from a source listed in privacy.tier3_sources."""
    tier3 = set((load_settings().get("privacy") or {}).get("tier3_sources") or [])
    platform = event.source.platform
    if platform == "slack" and event.content.metadata.get("slack_channel_type") == "dm":
        platform = "slack_dm"
    return platform in tier3


async def _router_model_is_cloud(space_id: str | None) -> bool:
    from laya.llm.client import _get_model_for_role, _get_space_model

    model = (
        await _get_space_model("router", space_id) if space_id else None
    ) or _get_model_for_role("router")
    return model.startswith(_CLOUD_PREFIXES) or "/" not in model


def extract_entities(event: LayaEvent) -> list[ExtractedEntity]:
    # subject.id carries the key itself on Jira/Linear events (e.g. "OPS-88").
    text = f"{event.subject.id}\n{event.subject.title}\n{event.content.body}"
    platform = event.source.platform
    found: list[ExtractedEntity] = []
    seen: set[str] = set()

    def add(entity_type: str, value: str) -> None:
        if value not in seen and len(found) < _MAX_ENTITIES:
            seen.add(value)
            found.append(
                ExtractedEntity(entity_type=entity_type, value=value, platform=platform)
            )

    for m in _TICKET_RE.finditer(text):
        add("ticket", m.group(0))
    for m in _PR_RE.finditer(text):
        add("pull_request", f"PR-{m.group(1)}")
    for m in _MENTION_RE.finditer(text):
        add("person", f"@{m.group(1)}")
    return found


def build_state(
    event: LayaEvent, actor_relationship: str, user_rules: str | None
) -> dict[str, str]:
    state = {
        "platform": event.source.platform,
        "event_type": event.source.raw_event_type,
        "actor_relationship": actor_relationship,
        "subject": f"[{event.subject.type}] {event.subject.title}",
        # Bodies can be whole email threads; Jev bills per input token and only needs
        # the gist to classify.
        "body": event.content.body[:6000],
    }
    if event.content.metadata:
        state["metadata"] = json.dumps(event.content.metadata, default=str)[:1000]
    state["user_rules"] = (user_rules or "none")[:3000]
    return state


def accept(answers: dict[str, Any], threshold: float) -> RouterOutput | None:
    """Turn Jev's answers into a RouterOutput, or None when the LLM should decide."""
    try:
        picks = {k: answers[k]["choice"] for k in ("category", "persona", "priority")}
        confs = [
            float(answers[k]["confidence"]) for k in ("category", "persona", "priority")
        ]
        research = float(answers["research"]["noul"])
    except (KeyError, TypeError, ValueError):
        return None
    # Research events need a research_plan, which only the LLM can write; an unsure
    # research answer (0.3-0.7) goes to the LLM too.
    if min(confs) < threshold or research >= 0.3:
        return None
    try:
        return RouterOutput(
            **picks,
            confidence=round(min(confs), 3),
            requires_research=False,
            reasoning=f"Jev: {picks['category']}/{picks['persona']}/{picks['priority']}, "
            f"min confidence {min(confs):.2f}, research p={research:.2f}",
        )
    except ValueError:
        return None  # a choice outside our enums


async def classify(
    event: LayaEvent,
    actor_relationship: str,
    user_rules: str | None = None,
    space_id: str | None = None,
) -> RouterOutput | None:
    """Classify with Jev. None means the caller must fall back to the LLM router."""
    cfg = get_jev_config()
    if not cfg["enabled"]:
        return None
    # Jev never sends out an event the configured router would have kept local.
    if is_private_source(event) and not await _router_model_is_cloud(space_id):
        return None
    key = _api_key()
    if not key:
        log.warning("jev_no_openrouter_key")
        return None

    from laya.http_client import get_client
    from laya.llm.client import log_to_audit

    model = cfg["model"]
    body = {
        "model": model,
        "state": build_state(event, actor_relationship, user_rules),
        "questions": _QUESTIONS,
    }
    start = time.monotonic()
    try:
        resp = await get_client().post(
            JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=10.0
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        log.warning("jev_call_failed", event_id=event.event_id, error=str(e))
        await log_to_audit(
            event.event_id, None, "route", model, 0, 0, ms, False, str(e)[:500]
        )
        return None

    ms = int((time.monotonic() - start) * 1000)
    usage = data.get("usage") or {}
    output = accept(data.get("answers") or {}, float(cfg["threshold"]))
    # Output tokens are logged as 0: Jev does not bill them, and the budget prices
    # audit_log tokens through MODEL_PRICING.
    await log_to_audit(
        event.event_id,
        None,
        "route",
        model,
        int(usage.get("input_tokens") or 0),
        0,
        ms,
        True,
        metadata={
            "backend": "jev",
            "accepted": output is not None,
            "cost": usage.get("cost"),
            "answers": data.get("answers"),
        },
    )
    if output is None:
        log.info("jev_deferred_to_llm", event_id=event.event_id, latency_ms=ms)
        return None
    output.entities = extract_entities(event)
    return output
