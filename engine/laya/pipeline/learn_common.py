# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the classification and context learners.

`learn.py` (classification corrections) and `context_learn.py` (context/link
corrections) run structurally identical "gather spaces with enough unprocessed
corrections" queries that differ only in table name and log-event label. This
module holds the one implementation so the two learners can't drift (review
§5.9 — P7-8).
"""

from __future__ import annotations

import structlog

from laya.db.sqlite import get_db

log = structlog.get_logger()


async def query_spaces_with_unprocessed(
    table: str,
    threshold: int,
    log_event: str,
) -> list:
    """Return space_ids in ``table`` with >= ``threshold`` unprocessed corrections.

    Args:
        table: Corrections table name (``classification_corrections`` or
            ``context_corrections``). Caller-supplied literal — never user input.
        threshold: Minimum unprocessed count for a space to qualify.
        log_event: structlog event name to emit if the query fails.
    """
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            f"""SELECT space_id, COUNT(*) as cnt
               FROM {table}
               WHERE processed = 0
               GROUP BY space_id
               HAVING cnt >= ?""",
            (threshold,),
        )
    except Exception as e:
        log.warning(log_event, error=str(e))
        return []

    return [r["space_id"] for r in rows]
