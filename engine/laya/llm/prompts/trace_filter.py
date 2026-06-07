# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""LLM prompt for Trace relevance filtering — second-pass false-positive reduction."""

from __future__ import annotations

from laya.llm.prompts.overrides import get_prompt


RELEVANCE_FILTER_SYSTEM = """\
You are a relevance judge for a work search system. Given a user's search query \
and a list of candidate results, determine which candidates are substantively \
related to the query.

A candidate is relevant ONLY if it is clearly about the same work item, project, \
topic, or issue that the query describes. Semantic similarity alone is not enough — \
the candidate must be meaningfully connected to what the user is looking for.

Be strict: when in doubt, mark as not relevant. It is better to miss a borderline \
result than to include an irrelevant one.\
"""

RELEVANCE_FILTER_SCHEMA = {
    "name": "trace_relevance_filter",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "judgments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "seed_index": {"type": "integer"},
                        "relevant": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["seed_index", "relevant", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["judgments"],
        "additionalProperties": False,
    },
}


def build_relevance_filter_messages(
    query: str,
    candidates: list[dict],
) -> list[dict[str, str]]:
    """Build messages for the relevance filter LLM call.

    Args:
        query: The user's trace search query.
        candidates: List of dicts with keys: seed_index, header, summary.

    Returns:
        [system_message, user_message] for llm_call().
    """
    lines = [f'Search query: "{query}"', "", "Candidates:"]
    for c in candidates:
        header = c["header"][:200] if c.get("header") else "(no header)"
        summary = c["summary"][:200] if c.get("summary") else "(no summary)"
        lines.append(
            f'{c["seed_index"]}. Header: "{header}" | Summary: "{summary}"'
        )

    lines.append("")
    lines.append(
        "For each candidate, judge whether it is relevant to the search query. "
        "Respond with JSON matching the schema."
    )

    return [
        {"role": "system", "content": get_prompt("trace_filter", RELEVANCE_FILTER_SYSTEM)},
        {"role": "user", "content": "\n".join(lines)},
    ]
