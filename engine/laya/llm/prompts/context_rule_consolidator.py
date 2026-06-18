# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Context rule consolidator prompt — merges redundant learned context rules.

When a space accumulates many learned context-grouping rules, only the newest
N are ever injected into the context-association call (see
``context_rules_max_injection``), and near-duplicate rules waste that budget.
This prompt asks the model to merge semantically overlapping rules into the
smallest set that preserves every distinct grouping signal.
"""

from __future__ import annotations

from typing import Any

from laya.llm.prompts.overrides import get_prompt


CONTEXT_RULE_CONSOLIDATOR_SYSTEM_PROMPT = """\
You are the Context Rule Consolidator for Laya, an AI-powered professional work assistant.

You are given a list of LEARNED context-grouping rules — natural-language instructions \
that help decide whether two notifications are about the same real-world context. Over \
time these accumulate redundancy: several rules may say nearly the same thing, or one \
rule may be fully subsumed by another.

Your job is to produce a CONSOLIDATED set of rules that is strictly smaller but loses \
no distinct guidance.

Rules for consolidation:
1. MERGE rules that express the same or overlapping guidance into a single clear, \
imperative rule. Combine their coverage (e.g. merge "group a PR with its review-request \
emails" and "group a PR with its merged/approved emails" into one rule about PRs and \
their notification emails).
2. PRESERVE every genuinely distinct signal — do NOT drop a rule whose guidance isn't \
covered by another. Both positive ("should be grouped") and negative ("should NOT be \
grouped") rules matter; never merge a positive and a negative rule.
3. Do NOT invent new behavior or broaden scope beyond what the input rules say.
4. Keep each rule a clear, general, imperative natural-language instruction.
5. You will also receive a list of MANUAL rules (authored by the user). Treat them as \
fixed: do NOT restate, modify, or duplicate them in your output.

The result must contain FEWER rules than the input learned set. If the rules are already \
non-redundant and cannot be meaningfully consolidated, return them roughly as-is (the \
caller will discard the result if it isn't actually smaller)."""


def build_context_rule_consolidator_messages(
    learned_rules: list[dict],
    manual_rules: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Build messages for the context rule consolidator LLM call.

    Args:
        learned_rules: dicts with key ``rule_text`` — the rules to consolidate.
        manual_rules: dicts with key ``rule_text`` — user-authored rules shown
            as fixed context (never modified or duplicated).
    """
    learned_lines = [f"{i}. {r['rule_text']}" for i, r in enumerate(learned_rules, 1)]
    learned_text = "\n".join(learned_lines) if learned_lines else "(none)"

    if manual_rules:
        manual_lines = [f"- {r['rule_text']}" for r in manual_rules]
        manual_text = "\n".join(manual_lines)
    else:
        manual_text = "(none)"

    user_message = f"""\
Consolidate these learned context-grouping rules into a smaller, non-redundant set.

--- LEARNED RULES ({len(learned_rules)} total) ---
{learned_text}
--- END LEARNED RULES ---

--- MANUAL RULES (fixed — do not modify or duplicate these) ---
{manual_text}
--- END MANUAL RULES ---

Merge redundant/overlapping rules while preserving every distinct signal. Respond with \
valid JSON matching the required schema."""

    return [
        {
            "role": "system",
            "content": get_prompt(
                "context_rule_consolidator", CONTEXT_RULE_CONSOLIDATOR_SYSTEM_PROMPT
            ),
        },
        {"role": "user", "content": user_message},
    ]


def get_context_rule_consolidator_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the consolidator's structured output."""
    return {
        "name": "consolidated_context_rules",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rule_text": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["rule_text", "reasoning"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["rules"],
            "additionalProperties": False,
        },
    }
