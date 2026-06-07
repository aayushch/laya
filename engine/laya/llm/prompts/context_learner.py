# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Context association learner prompt — extracts grouping rules from user link/unlink actions."""

from __future__ import annotations

from typing import Any

from laya.llm.prompts.overrides import get_prompt


CONTEXT_LEARNER_SYSTEM_PROMPT = """\
You are the Context Association Learner for Laya, an AI-powered professional work assistant.

Your job is to analyze a batch of user linking and unlinking actions on notification cards \
and extract generalized rules that can improve future context grouping accuracy.

Each action shows:
- Two card headers and summaries (what the cards were about)
- The platforms they came from
- Whether the user LINKED them (they belong to the same context) or UNLINKED them \
(they were incorrectly grouped together)

Your task:
1. Look for REPEATABLE PATTERNS in what the user links or unlinks
2. Only create a rule when you see 2 or more similar actions supporting it
3. Express each rule as a clear, imperative, natural-language instruction
4. Provide brief reasoning explaining which actions support the rule

Rules should help the AI decide whether two notifications are about the same real-world \
context. Focus on what makes cards contextually related (or not) from the user's perspective.

Good examples:
- "Bills and payment receipts for the same service provider should be grouped together"
- "Job application notifications and interview scheduling emails from the same company \
should be grouped together"
- "Marketing newsletters from different companies should NOT be grouped even if they \
are about similar topics"
- "Bank transaction alerts and monthly statements should be grouped by account"
- "A Jira or Linear ticket, its email notifications, and Slack messages that reference \
the same ticket ID (e.g., 'PROJ-123') should be grouped together"
- "A GitHub or Bitbucket pull request and its email notifications (review requested, \
approved, changes requested, merged, commented) should be grouped together"
- "Commit/push emails and CI build alerts referencing the same pull request or commit \
SHA should be grouped together"
- "A calendar invite and the email or Slack discussion about the same meeting should \
be grouped together"
- "Incident/on-call pages (PagerDuty, Slack alerts, monitoring emails) referencing the \
same incident ID should be grouped together"
- "Different pull requests in the same repository should NOT be grouped just because \
they share the repo — only events about the SAME PR belong together"
- "Different Jira/Linear tickets in the same project should NOT be grouped just because \
they share a project/team key"
- "Daily standup reminders or recurring meeting invites should NOT be grouped across \
different days — each day's occurrence is its own context"

Bad examples (too specific or unsupported):
- "The SP Utilities bill and PayNow receipt should be linked" (too specific to one instance)
- "All emails should be grouped together" (too broad)

IMPORTANT: You will also receive a list of existing rules. Do NOT create rules that \
duplicate or closely overlap with existing ones. Only propose genuinely new patterns."""


def build_context_learner_messages(
    corrections: list[dict],
    existing_rules: list[dict],
) -> list[dict[str, str]]:
    """Build messages for the context learner LLM call.

    Args:
        corrections: List of context correction dicts with keys: action, header_a,
            summary_a, platform_a, header_b, summary_b, platform_b.
        existing_rules: List of existing context rule dicts with keys: rule_text.
    """
    correction_lines = []
    for i, c in enumerate(corrections, 1):
        action = c.get("action", "link").upper()
        platform_a = c.get("platform_a", "?")
        platform_b = c.get("platform_b", "?")
        header_a = c.get("header_a", "unknown")
        header_b = c.get("header_b", "unknown")
        summary_a = (c.get("summary_a") or "")[:200]
        summary_b = (c.get("summary_b") or "")[:200]

        correction_lines.append(
            f"{i}. [{action}] ({platform_a} + {platform_b})\n"
            f"   Card A: \"{header_a}\"\n"
            f"   {summary_a}\n"
            f"   Card B: \"{header_b}\"\n"
            f"   {summary_b}"
        )

    corrections_text = "\n".join(correction_lines)

    if existing_rules:
        rules_lines = [f"{i}. {r['rule_text']}" for i, r in enumerate(existing_rules, 1)]
        existing_text = "\n".join(rules_lines)
    else:
        existing_text = "(none)"

    user_message = f"""\
Analyze these user linking/unlinking actions and extract context grouping rules.

--- USER ACTIONS ({len(corrections)} total) ---
{corrections_text}
--- END ACTIONS ---

--- EXISTING RULES (do not duplicate) ---
{existing_text}
--- END EXISTING RULES ---

Extract rules from the patterns you observe. Respond with valid JSON matching the required schema. \
If no clear patterns exist (actions are all one-off), return an empty rules array."""

    return [
        {"role": "system", "content": get_prompt("context_learner", CONTEXT_LEARNER_SYSTEM_PROMPT)},
        {"role": "user", "content": user_message},
    ]


def get_context_learner_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the context learner's structured output."""
    return {
        "name": "context_rules",
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
