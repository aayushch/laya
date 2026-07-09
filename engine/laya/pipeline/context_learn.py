# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Context association learner — extracts grouping rules from user link/unlink actions."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.context_learner import (
    build_context_learner_messages,
    get_context_learner_json_schema,
)
from laya.llm.prompts.context_rule_consolidator import (
    build_context_rule_consolidator_messages,
    get_context_rule_consolidator_json_schema,
)
from laya.pipeline.learn_common import (
    fetch_unprocessed_corrections,
    mark_corrections_processed,
    query_spaces_with_unprocessed,
)

log = structlog.get_logger()

# Defaults — configurable via settings.json tuning section
def _correction_threshold() -> int:
    from laya.config import get_tuning
    return get_tuning("context_learn_threshold")

def _batch_limit() -> int:
    from laya.config import get_tuning
    return get_tuning("context_learn_batch")


async def get_spaces_with_unprocessed(threshold: int | None = None) -> list:
    """Return space_ids that have enough unprocessed context corrections."""
    if threshold is None:
        threshold = _correction_threshold()
    return await query_spaces_with_unprocessed(
        "context_corrections", threshold, "context_learn_query_spaces_failed"
    )


async def run_context_learn_extraction(space_id: str | None) -> int:
    """Analyze unprocessed context corrections for a space and extract rules.

    Returns the number of new rules created.
    """
    db = await get_db()

    # 1. Fetch unprocessed corrections (oldest first)
    corrections = await fetch_unprocessed_corrections(
        "context_corrections",
        "id, action, header_a, summary_a, platform_a, header_b, summary_b, platform_b",
        space_id,
        _batch_limit(),
    )

    if not corrections:
        return 0

    correction_ids = [c["id"] for c in corrections]

    # 2. Fetch existing active rules (to avoid duplication)
    existing_rows = await db.execute_fetchall(
        """SELECT rule_text FROM context_rules
           WHERE active = 1 AND (space_id IS NULL OR space_id = ?)""",
        (space_id,),
    )
    existing_rules = [dict(r) for r in existing_rows]

    # 3. Call LLM to extract rules
    messages = build_context_learner_messages(corrections, existing_rules)
    schema = get_context_learner_json_schema()

    try:
        response = await llm_call(
            role="router",  # cheap model
            messages=messages,
            response_schema=schema,
            step="context_learn",
            temperature=0.2,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
    except Exception as e:
        log.error("context_learn_llm_failed", error=str(e), space_id=space_id)
        return 0

    if not response.parsed:
        log.warning("context_learn_no_parsed_response", space_id=space_id)
        return 0

    # 4. Store new rules
    now = db_now()
    new_rules = 0
    for rule in response.parsed.get("rules", []):
        rule_text = rule.get("rule_text", "").strip()
        if not rule_text:
            continue

        await db.execute(
            """INSERT INTO context_rules
               (space_id, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, 'learned', 1, ?, ?)""",
            (space_id, rule_text, now, now),
        )
        new_rules += 1
        log.info(
            "context_rule_learned",
            rule_text=rule_text[:80],
            reasoning=rule.get("reasoning", "")[:100],
            space_id=space_id,
        )

    # 5. Mark corrections as processed
    await mark_corrections_processed("context_corrections", correction_ids)

    await db.commit()
    log.info(
        "context_learn_complete",
        space_id=space_id,
        corrections_processed=len(correction_ids),
        rules_created=new_rules,
    )

    # Consolidate if this space's learned rules have grown large. Done here
    # (right after the growth) so it rides the existing 6-hourly learn pass and
    # needs no separate scheduling. Failures must not break the learn run.
    try:
        await maybe_consolidate_context_rules(space_id)
    except Exception as e:
        log.error("context_rules_consolidate_failed", space_id=space_id, error=str(e))

    return new_rules


def _consolidation_threshold() -> int:
    from laya.config import get_tuning
    return get_tuning("context_rules_consolidation_threshold")


async def _load_scoped_rules(db, source: str, space_id: str | None) -> list[dict]:
    """Load active rules of a given source for an exact space scope.

    Matches how extraction inserts rules: a NULL space_id selects the global
    rules, a concrete space_id selects only that space's rules (never both),
    so consolidation only ever rewrites the scope it was triggered for.
    """
    if space_id is not None:
        rows = await db.execute_fetchall(
            """SELECT id, rule_text FROM context_rules
               WHERE source = ? AND active = 1 AND space_id = ?
               ORDER BY created_at ASC""",
            (source, space_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT id, rule_text FROM context_rules
               WHERE source = ? AND active = 1 AND space_id IS NULL
               ORDER BY created_at ASC""",
            (source,),
        )
    return [dict(r) for r in rows]


async def maybe_consolidate_context_rules(space_id: str | None) -> int:
    """Merge redundant learned context rules for a space when they grow large.

    Loads the active learned rules for the exact space scope; if their count
    exceeds the consolidation threshold, asks the LLM to merge redundant rules
    into a smaller canonical set and replaces them transactionally. Manual
    (user-authored) rules are never modified — they are passed to the LLM only
    as fixed context. Returns the consolidated rule count, or 0 if it was a
    no-op (below threshold, LLM failure, or guardrail trip).
    """
    db = await get_db()

    learned = await _load_scoped_rules(db, "learned", space_id)
    if len(learned) <= _consolidation_threshold():
        return 0

    manual = await _load_scoped_rules(db, "manual", space_id)

    messages = build_context_rule_consolidator_messages(learned, manual)
    schema = get_context_rule_consolidator_json_schema()

    try:
        response = await llm_call(
            role="router",  # cheap model
            messages=messages,
            response_schema=schema,
            step="context_consolidate",
            temperature=0.2,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
    except Exception as e:
        log.error("context_consolidate_llm_failed", error=str(e), space_id=space_id)
        return 0

    if not response.parsed:
        log.warning("context_consolidate_no_parsed_response", space_id=space_id)
        return 0

    consolidated = [
        r.get("rule_text", "").strip()
        for r in response.parsed.get("rules", [])
        if r.get("rule_text", "").strip()
    ]

    # Guardrails: never apply a result that drops all rules or fails to shrink
    # the set — that's either useless or a sign the model misbehaved. Leaving
    # the originals in place is always safe.
    if not consolidated or len(consolidated) >= len(learned):
        log.info(
            "context_consolidate_skipped",
            space_id=space_id,
            before=len(learned),
            proposed=len(consolidated),
        )
        return 0

    # Replace transactionally: delete exactly the learned ids we loaded (so any
    # rule inserted concurrently is untouched), then insert the consolidated
    # set. Manual rules are never deleted.
    now = db_now()
    learned_ids = [r["id"] for r in learned]
    placeholders = ",".join("?" * len(learned_ids))
    await db.execute(
        f"DELETE FROM context_rules WHERE id IN ({placeholders})",
        learned_ids,
    )
    for rule_text in consolidated:
        await db.execute(
            """INSERT INTO context_rules
               (space_id, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, 'learned', 1, ?, ?)""",
            (space_id, rule_text, now, now),
        )
    await db.commit()

    log.info(
        "context_rules_consolidated",
        space_id=space_id,
        before=len(learned),
        after=len(consolidated),
    )
    return len(consolidated)


async def run_context_learn_all() -> None:
    """Run context learning for all spaces with enough unprocessed corrections."""
    spaces = await get_spaces_with_unprocessed()
    if not spaces:
        return

    total_rules = 0
    for space_id in spaces:
        try:
            count = await run_context_learn_extraction(space_id)
            total_rules += count
        except Exception as e:
            log.error(
                "context_learn_space_failed",
                space_id=space_id,
                error=str(e),
            )

    if total_rules:
        log.info("context_learn_all_complete", total_rules=total_rules)


async def query_context_rules(space_id: str | None) -> list[dict]:
    """Query active context rules for injection into the context grouping prompt.

    Returns rules applicable to the given space (space-specific + global).
    """
    from laya.config import get_tuning
    max_rules = get_tuning("context_rules_max_injection")
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT rule_text, source FROM context_rules
               WHERE active = 1 AND (space_id IS NULL OR space_id = ?)
               ORDER BY created_at DESC
               LIMIT ?""",
            (space_id, max_rules),
        )
    except Exception as e:
        log.warning("context_rules_query_failed", error=str(e))
        return []

    return [dict(r) for r in rows]


async def query_recent_context_corrections(
    space_id: str | None,
    limit: int = 10,
) -> list[dict]:
    """Query recent context corrections for injection into the context grouping prompt.

    Returns the most recent link/unlink actions for learning signal.
    """
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT action, header_a, header_b, platform_a, platform_b
               FROM context_corrections
               WHERE space_id IS NULL OR space_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (space_id, limit),
        )
    except Exception as e:
        log.warning("context_corrections_query_failed", error=str(e))
        return []

    return [dict(r) for r in rows]


def format_context_feedback_section(
    rules: list[dict] | None = None,
    corrections: list[dict] | None = None,
) -> str | None:
    """Format context rules and corrections into a prompt section.

    Returns a formatted string for injection into the context grouping
    LLM call, or None if nothing to inject.
    """
    sections: list[str] = []

    if rules:
        lines = ["--- CONTEXT GROUPING RULES (always follow these) ---"]
        for i, rule in enumerate(rules, 1):
            lines.append(f"{i}. {rule['rule_text']}")
        lines.append("--- END RULES ---")
        sections.append("\n".join(lines))

    if corrections:
        lines = ["--- RECENT USER LINKING ACTIONS (learn from these) ---"]
        for i, c in enumerate(corrections, 1):
            action = c["action"].upper()
            lines.append(
                f"{i}. [{action}] \"{c.get('header_a', '?')}\" ({c.get('platform_a', '?')}) "
                f"{'<->' if action == 'LINK' else '=/='} "
                f"\"{c.get('header_b', '?')}\" ({c.get('platform_b', '?')})"
            )
        lines.append("--- END ACTIONS ---")
        sections.append("\n".join(lines))

    if not sections:
        return None

    return "\n\n".join(sections)
