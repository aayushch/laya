# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Learner prompt — extracts classification rules from user correction patterns."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts import current_timestamp_line
from laya.llm.prompts.overrides import get_prompt


LEARNER_SYSTEM_PROMPT = """\
You are the Classification Learner for Laya, an AI-powered professional work assistant.

Your job is to analyze a batch of user corrections to card classifications and extract \
generalized rules that can improve future classification accuracy.

Each correction shows:
- A card summary (what the card was about)
- The platform and event type (e.g., jira/issue_status_changed)
- The field that was corrected (priority or persona)
- The original value (what the AI assigned) and the corrected value (what the user changed it to)

Your task:
1. Look for REPEATABLE PATTERNS — corrections that share a common trait (same platform, \
same event type, same actor, same category of content, etc.)
2. Only create a rule when you see 2 or more similar corrections supporting it
3. Express each rule as a clear, imperative, natural-language instruction
4. Indicate which field the rule applies to (priority or persona)
5. Provide brief reasoning explaining which corrections support the rule

Rules should be specific enough to be actionable but general enough to apply to future events. \
Good rules target a category of events, not a single card.

Good examples:
- "Jira issue_status_changed events should be classified as LOW priority unless transitioning to a blocked state"
- "PR approval notifications from Bitbucket should always be LOW priority"
- "Emails from external contacts should use the COMMS persona, not ENGINEER"

Bad examples (too specific or unsupported):
- "Card about PROJ-1234 should be HIGH" (too specific to one card)
- "Everything should be LOW priority" (too broad, unlikely supported by data)

IMPORTANT: You will also receive a list of existing rules. Do NOT create rules that \
duplicate or closely overlap with existing ones. Only propose genuinely new patterns."""


def build_learner_messages(
    corrections: list[dict],
    existing_rules: list[dict],
) -> list[dict[str, str]]:
    """Build messages for the learner LLM call.

    Args:
        corrections: List of correction dicts with keys: field, original_value,
            corrected_value, card_summary, platform, event_type, category.
        existing_rules: List of existing rule dicts with keys: field, rule_text.
    """
    # Format corrections
    correction_lines = []
    for i, c in enumerate(corrections, 1):
        summary = c.get("card_summary") or "unknown"
        ctx = f"{c.get('platform', '?')}/{c.get('event_type', '?')}"
        category = c.get("category", "?")
        correction_lines.append(
            f"{i}. [{ctx}] (category: {category}) \"{summary}\"\n"
            f"   {c['field']}: {c['original_value']} → {c['corrected_value']}"
        )

    corrections_text = "\n".join(correction_lines)

    # Format existing rules
    if existing_rules:
        rules_lines = []
        for i, r in enumerate(existing_rules, 1):
            prefix = f"[{r['field']}] " if r.get("field") else ""
            rules_lines.append(f"{i}. {prefix}{r['rule_text']}")
        existing_text = "\n".join(rules_lines)
    else:
        existing_text = "(none)"

    user_message = f"""\
{current_timestamp_line()}

Analyze these user corrections and extract classification rules.

--- CORRECTIONS ({len(corrections)} total) ---
{corrections_text}
--- END CORRECTIONS ---

--- EXISTING RULES (do not duplicate) ---
{existing_text}
--- END EXISTING RULES ---

Extract rules from the patterns you observe. Respond with valid JSON matching the required schema. \
If no clear patterns exist (corrections are all one-off), return an empty rules array."""

    return [
        {"role": "system", "content": get_prompt("learner", LEARNER_SYSTEM_PROMPT)},
        {"role": "user", "content": user_message},
    ]


def get_learner_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the learner's structured output."""
    return {
        "name": "learned_rules",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {
                                "type": "string",
                                "enum": ["priority", "persona"],
                            },
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
