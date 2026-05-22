# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Classification learner — extracts rules from accumulated user corrections."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.learner import build_learner_messages, get_learner_json_schema

log = structlog.get_logger()

# Defaults — configurable via settings.json tuning section
def _correction_threshold() -> int:
    from laya.config import get_tuning
    return get_tuning("classification_learn_threshold", 15)

def _batch_limit() -> int:
    from laya.config import get_tuning
    return get_tuning("classification_learn_batch", 50)


async def get_spaces_with_unprocessed(threshold: int | None = None) -> list:
    """Return space_ids that have enough unprocessed corrections for rule extraction."""
    if threshold is None:
        threshold = _correction_threshold()
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT space_id, COUNT(*) as cnt
               FROM classification_corrections
               WHERE processed = 0
               GROUP BY space_id
               HAVING cnt >= ?""",
            (threshold,),
        )
    except Exception as e:
        log.warning("learn_query_spaces_failed", error=str(e))
        return []

    return [r["space_id"] for r in rows]


async def run_learn_extraction(space_id: str | None) -> int:
    """Analyze unprocessed corrections for a space and extract learned rules.

    Returns the number of new rules created.
    """
    db = await get_db()

    # 1. Fetch unprocessed corrections (oldest first)
    if space_id is not None:
        corrections_rows = await db.execute_fetchall(
            """SELECT id, field, original_value, corrected_value,
                      card_summary, category, platform, event_type
               FROM classification_corrections
               WHERE processed = 0 AND space_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (space_id, _batch_limit()),
        )
    else:
        corrections_rows = await db.execute_fetchall(
            """SELECT id, field, original_value, corrected_value,
                      card_summary, category, platform, event_type
               FROM classification_corrections
               WHERE processed = 0 AND space_id IS NULL
               ORDER BY created_at ASC
               LIMIT ?""",
            (_batch_limit(),),
        )

    if not corrections_rows:
        return 0

    corrections = [
        {
            "id": r["id"],
            "field": r["field"],
            "original_value": r["original_value"],
            "corrected_value": r["corrected_value"],
            "card_summary": r["card_summary"],
            "category": r["category"],
            "platform": r["platform"],
            "event_type": r["event_type"],
        }
        for r in corrections_rows
    ]
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
        max_tokens=2000,
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

    now = datetime.now(timezone.utc).isoformat()
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
    placeholders = ",".join("?" * len(correction_ids))
    await db.execute(
        f"UPDATE classification_corrections SET processed = 1 WHERE id IN ({placeholders})",
        tuple(correction_ids),
    )

    await db.commit()

    log.info(
        "learn_extraction_complete",
        space_id=space_id,
        corrections_processed=len(correction_ids),
        rules_created=new_rules,
    )

    return new_rules


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
