# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Classification rule consolidator prompt — merges redundant learned rules.

Learned classification rules (priority/persona guidance extracted from user
corrections) accumulate redundancy over time. Only the newest N are injected into
the router call (see ``classification_rules_max_injection``), so near-duplicate
rules waste that budget and can crowd out distinct guidance. This prompt asks the
model to merge semantically overlapping rules into the smallest set that
preserves every distinct signal — the classification-side twin of the context
rule consolidator.
"""

from __future__ import annotations

from typing import Any

from laya.llm.prompts.overrides import get_prompt


CLASSIFICATION_RULE_CONSOLIDATOR_SYSTEM_PROMPT = """\
You are the Classification Rule Consolidator for Laya, an AI-powered professional work assistant.

You are given a list of LEARNED classification rules — natural-language instructions that \
guide how events are classified. Each rule applies to ONE field: either `priority` (how \
urgent) or `persona` (which specialist handles it). Over time these accumulate redundancy: \
several rules may say nearly the same thing, or one rule may be fully subsumed by another.

Your job is to produce a CONSOLIDATED set of rules that is strictly smaller but loses no \
distinct guidance.

Rules for consolidation:
1. MERGE rules that express the same or overlapping guidance into a single clear, \
imperative rule (e.g. merge "PR approvals from Bitbucket should be LOW priority" and "PR \
approval notifications should be LOW priority" into one).
2. Only ever merge rules for the SAME field. A `priority` rule and a `persona` rule are \
never redundant with each other — keep them separate, and every output rule MUST keep the \
field it belongs to.
3. PRESERVE every genuinely distinct signal — do NOT drop a rule whose guidance isn't \
covered by another.
4. Do NOT invent new behavior or broaden scope beyond what the input rules say.
5. Keep each rule a clear, general, imperative natural-language instruction.

The result must contain FEWER rules than the input learned set. If the rules are already \
non-redundant and cannot be meaningfully consolidated, return them roughly as-is (the \
caller will discard the result if it isn't actually smaller)."""


def build_classification_rule_consolidator_messages(
    learned_rules: list[dict],
) -> list[dict[str, str]]:
    """Build messages for the classification rule consolidator LLM call.

    Args:
        learned_rules: dicts with keys ``field`` (priority|persona) and
            ``rule_text`` — the rules to consolidate. Grouped by field in the
            prompt so the model consolidates within a field, never across.
    """
    by_field: dict[str, list[str]] = {}
    for r in learned_rules:
        by_field.setdefault(r.get("field", "?"), []).append(r["rule_text"])

    blocks = []
    for field in ("priority", "persona"):
        texts = by_field.get(field)
        if not texts:
            continue
        lines = [f"[{field}]"] + [f"{i}. {t}" for i, t in enumerate(texts, 1)]
        blocks.append("\n".join(lines))
    learned_text = "\n\n".join(blocks) if blocks else "(none)"

    user_message = f"""\
Consolidate these learned classification rules into a smaller, non-redundant set. \
Merge only WITHIN a field; keep each output rule's field.

--- LEARNED RULES ({len(learned_rules)} total) ---
{learned_text}
--- END LEARNED RULES ---

Merge redundant/overlapping rules while preserving every distinct signal. Respond with \
valid JSON matching the required schema."""

    return [
        {
            "role": "system",
            "content": get_prompt(
                "classification_rule_consolidator",
                CLASSIFICATION_RULE_CONSOLIDATOR_SYSTEM_PROMPT,
            ),
        },
        {"role": "user", "content": user_message},
    ]


def get_classification_rule_consolidator_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the consolidator's structured output."""
    return {
        "name": "consolidated_classification_rules",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": ["priority", "persona"]},
                            "rule_text": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["field", "rule_text", "reasoning"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["rules"],
            "additionalProperties": False,
        },
    }
