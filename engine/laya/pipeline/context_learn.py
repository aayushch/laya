"""Context association learner — extracts grouping rules from user link/unlink actions."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from laya.db.sqlite import get_db
from laya.llm.client import llm_call
from laya.llm.prompts.context_learner import (
    build_context_learner_messages,
    get_context_learner_json_schema,
)

log = structlog.get_logger()

# Minimum unprocessed corrections before triggering extraction
CORRECTION_THRESHOLD = 10

# Maximum corrections to send in a single LLM call
BATCH_LIMIT = 40


async def get_spaces_with_unprocessed(threshold: int = CORRECTION_THRESHOLD) -> list:
    """Return space_ids that have enough unprocessed context corrections."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT space_id, COUNT(*) as cnt
               FROM context_corrections
               WHERE processed = 0
               GROUP BY space_id
               HAVING cnt >= ?""",
            (threshold,),
        )
    except Exception as e:
        log.warning("context_learn_query_spaces_failed", error=str(e))
        return []

    return [r["space_id"] for r in rows]


async def run_context_learn_extraction(space_id: str | None) -> int:
    """Analyze unprocessed context corrections for a space and extract rules.

    Returns the number of new rules created.
    """
    db = await get_db()

    # 1. Fetch unprocessed corrections (oldest first)
    if space_id is not None:
        corrections_rows = await db.execute_fetchall(
            """SELECT id, action, header_a, summary_a, platform_a,
                      header_b, summary_b, platform_b
               FROM context_corrections
               WHERE processed = 0 AND space_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (space_id, BATCH_LIMIT),
        )
    else:
        corrections_rows = await db.execute_fetchall(
            """SELECT id, action, header_a, summary_a, platform_a,
                      header_b, summary_b, platform_b
               FROM context_corrections
               WHERE processed = 0 AND space_id IS NULL
               ORDER BY created_at ASC
               LIMIT ?""",
            (BATCH_LIMIT,),
        )

    if not corrections_rows:
        return 0

    corrections = [dict(r) for r in corrections_rows]
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
            max_tokens=2000,
        )
    except Exception as e:
        log.error("context_learn_llm_failed", error=str(e), space_id=space_id)
        return 0

    if not response.parsed:
        log.warning("context_learn_no_parsed_response", space_id=space_id)
        return 0

    # 4. Store new rules
    now = datetime.now(timezone.utc).isoformat()
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
    if correction_ids:
        placeholders = ",".join("?" * len(correction_ids))
        await db.execute(
            f"UPDATE context_corrections SET processed = 1 WHERE id IN ({placeholders})",
            correction_ids,
        )

    await db.commit()
    log.info(
        "context_learn_complete",
        space_id=space_id,
        corrections_processed=len(correction_ids),
        rules_created=new_rules,
    )
    return new_rules


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
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT rule_text, source FROM context_rules
               WHERE active = 1 AND (space_id IS NULL OR space_id = ?)
               ORDER BY created_at DESC
               LIMIT 20""",
            (space_id,),
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
