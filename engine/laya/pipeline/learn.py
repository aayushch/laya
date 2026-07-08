# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Classification learner — extracts rules from accumulated user corrections."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from laya.db.sqlite import get_db
from laya.db.timeutil import db_now
from laya.llm.client import DEFAULT_MAX_TOKENS, llm_call
from laya.llm.prompts.learner import build_learner_messages, get_learner_json_schema
from laya.llm.prompts.classification_rule_consolidator import (
    build_classification_rule_consolidator_messages,
    get_classification_rule_consolidator_json_schema,
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
    return get_tuning("classification_learn_threshold")

def _batch_limit() -> int:
    from laya.config import get_tuning
    return get_tuning("classification_learn_batch")


async def get_spaces_with_unprocessed(threshold: int | None = None) -> list:
    """Return space_ids that have enough unprocessed corrections for rule extraction."""
    if threshold is None:
        threshold = _correction_threshold()
    return await query_spaces_with_unprocessed(
        "classification_corrections", threshold, "learn_query_spaces_failed"
    )


async def run_learn_extraction(space_id: str | None) -> int:
    """Analyze unprocessed corrections for a space and extract learned rules.

    Returns the number of new rules created.
    """
    db = await get_db()

    # 1. Fetch unprocessed corrections (oldest first)
    corrections = await fetch_unprocessed_corrections(
        "classification_corrections",
        "id, field, original_value, corrected_value, card_summary, category, platform, event_type",
        space_id,
        _batch_limit(),
    )

    if not corrections:
        return 0

    correction_ids = [c["id"] for c in corrections]

    # 2. Fetch existing active rules (to avoid duplicates)
    if space_id is not None:
        rules_rows = await db.execute_fetchall(
            """SELECT field, rule_text FROM classification_rules
               WHERE active = 1 AND (space_id IS NULL OR space_id = ?)""",
            (space_id,),
        )
    else:
        rules_rows = await db.execute_fetchall(
            "SELECT field, rule_text FROM classification_rules WHERE active = 1"
        )

    existing_rules = [{"field": r["field"], "rule_text": r["rule_text"]} for r in rules_rows]

    # 3. Build prompt and call LLM
    messages = build_learner_messages(corrections, existing_rules)
    schema = get_learner_json_schema()

    log.info(
        "learn_extraction_started",
        space_id=space_id,
        correction_count=len(corrections),
        existing_rules=len(existing_rules),
    )

    response = await llm_call(
        role="router",
        messages=messages,
        response_schema=schema,
        step="learn",
        temperature=0.2,
        max_tokens=DEFAULT_MAX_TOKENS,
        space_id=space_id,
    )

    # 4. Parse response and store new rules
    new_rules = 0
    parsed = response.parsed
    if not parsed:
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, Exception) as e:
            log.warning("learn_parse_failed", error=str(e), space_id=space_id)
            parsed = {"rules": []}

    now = db_now()
    for rule in parsed.get("rules", []):
        rule_text = rule.get("rule_text", "").strip()
        field = rule.get("field")
        reasoning = rule.get("reasoning", "")

        if not rule_text:
            continue

        await db.execute(
            """INSERT INTO classification_rules
               (space_id, field, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, ?, 'learned', 1, ?, ?)""",
            (space_id, field, rule_text, now, now),
        )
        new_rules += 1
        log.info(
            "learn_rule_created",
            space_id=space_id,
            field=field,
            rule_text=rule_text[:100],
            reasoning=reasoning[:100],
        )

    # 5. Mark all fetched corrections as processed (even if 0 rules extracted)
    await mark_corrections_processed("classification_corrections", correction_ids)

    await db.commit()

    log.info(
        "learn_extraction_complete",
        space_id=space_id,
        corrections_processed=len(correction_ids),
        rules_created=new_rules,
    )

    # Consolidate if this space's learned rules have grown large. Done here (right
    # after the growth) so it rides the existing learn pass and needs no separate
    # scheduling. Failures must not break the learn run. Mirrors context_learn.py.
    try:
        await maybe_consolidate_classification_rules(space_id)
    except Exception as e:
        log.error("classification_rules_consolidate_failed", space_id=space_id, error=str(e))

    return new_rules


def _consolidation_threshold() -> int:
    from laya.config import get_tuning
    return get_tuning("classification_rules_consolidation_threshold")


async def _load_scoped_rules(db, source: str, space_id: str | None) -> list[dict]:
    """Load active rules of a given source for an exact space scope.

    Matches how extraction inserts rules: a NULL space_id selects the global
    rules, a concrete space_id selects only that space's rules (never both), so
    consolidation only ever rewrites the scope it was triggered for.
    """
    if space_id is not None:
        rows = await db.execute_fetchall(
            """SELECT id, field, rule_text FROM classification_rules
               WHERE source = ? AND active = 1 AND space_id = ?
               ORDER BY created_at ASC""",
            (source, space_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT id, field, rule_text FROM classification_rules
               WHERE source = ? AND active = 1 AND space_id IS NULL
               ORDER BY created_at ASC""",
            (source,),
        )
    return [dict(r) for r in rows]


async def maybe_consolidate_classification_rules(space_id: str | None) -> int:
    """Merge redundant learned classification rules for a space when they grow large.

    Loads the active learned rules for the exact space scope; if their count
    exceeds the consolidation threshold, asks the LLM to merge redundant rules
    (WITHIN each field — priority/persona are never merged across) into a smaller
    canonical set and replaces them transactionally. Manual rules are never
    touched. Returns the consolidated rule count, or 0 on a no-op (below
    threshold, LLM failure, or guardrail trip). Mirrors context_learn.py.
    """
    db = await get_db()

    learned = await _load_scoped_rules(db, "learned", space_id)
    if len(learned) <= _consolidation_threshold():
        return 0

    messages = build_classification_rule_consolidator_messages(learned)
    schema = get_classification_rule_consolidator_json_schema()

    try:
        response = await llm_call(
            role="router",  # cheap model
            messages=messages,
            response_schema=schema,
            step="classification_consolidate",
            temperature=0.2,
            max_tokens=DEFAULT_MAX_TOKENS,
            space_id=space_id,
        )
    except Exception as e:
        log.error("classification_consolidate_llm_failed", error=str(e), space_id=space_id)
        return 0

    if not response.parsed:
        log.warning("classification_consolidate_no_parsed_response", space_id=space_id)
        return 0

    consolidated = [
        {"field": r.get("field"), "rule_text": r.get("rule_text", "").strip()}
        for r in response.parsed.get("rules", [])
        if r.get("rule_text", "").strip() and r.get("field") in ("priority", "persona")
    ]

    # Guardrails: never apply a result that drops all rules or fails to shrink the
    # set — that's either useless or a misbehaving model. Keeping the originals is
    # always safe.
    if not consolidated or len(consolidated) >= len(learned):
        log.info(
            "classification_consolidate_skipped",
            space_id=space_id,
            before=len(learned),
            proposed=len(consolidated),
        )
        return 0

    # Replace transactionally: delete exactly the learned ids we loaded (so a rule
    # inserted concurrently is untouched), then insert the consolidated set.
    now = db_now()
    learned_ids = [r["id"] for r in learned]
    placeholders = ",".join("?" * len(learned_ids))
    await db.execute(
        f"DELETE FROM classification_rules WHERE id IN ({placeholders})",
        tuple(learned_ids),
    )
    for rule in consolidated:
        await db.execute(
            """INSERT INTO classification_rules
               (space_id, field, rule_text, source, active, created_at, updated_at)
               VALUES (?, ?, ?, 'learned', 1, ?, ?)""",
            (space_id, rule["field"], rule["rule_text"], now, now),
        )
    await db.commit()

    log.info(
        "classification_rules_consolidated",
        space_id=space_id,
        before=len(learned),
        after=len(consolidated),
    )
    return len(consolidated)


async def run_learn_all() -> None:
    """Entry point for scheduler — extract learned rules for all eligible spaces."""
    spaces = await get_spaces_with_unprocessed()
    if not spaces:
        log.debug("learn_no_eligible_spaces")
        return

    log.info("learn_extraction_triggered", eligible_spaces=len(spaces))

    total_rules = 0
    for space_id in spaces:
        try:
            count = await run_learn_extraction(space_id)
            total_rules += count
        except Exception as e:
            log.error("learn_extraction_space_failed", space_id=space_id, error=str(e))

    log.info("learn_extraction_all_complete", total_rules=total_rules)
