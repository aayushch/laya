"""Platform action filters that drop events before any model call.

GitHub has an opt-in filter (``pipeline.github_actionable_only``) that keeps only
events concerning the Laya user. The per-platform module computes the facts; this
module is the single entry point the queue calls.
"""

from laya.config import github_action_filter_enabled
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent
from laya.pipeline.github_addressing import github_addressing


async def unaddressed_filter_rule(event: LayaEvent, space_id: str | None) -> str | None:
    """Return the filter rule text when an action filter drops ``event``, else None.

    None also covers platforms without a filter and platforms whose filter is off.
    """
    if not github_action_filter_enabled(event.source.platform):
        return None

    addressing = await github_addressing(event, space_id)
    if addressing is None or addressing.addressed:
        return None

    # A queued reprocess must finish updating its existing card even after a
    # restart. The addressing gate only decides whether to create a new card.
    db = await get_db()
    existing_card = await db.execute_fetchall(
        "SELECT 1 FROM action_cards WHERE event_id = ? LIMIT 1", (event.event_id,),
    )
    return None if existing_card else addressing.filter_rule
