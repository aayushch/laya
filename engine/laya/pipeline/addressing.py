"""Platform action filters that drop events before any model call.

GitHub has an opt-in filter (``pipeline.github_actionable_only``) that keeps only
events concerning the Laya user. The per-platform module computes the facts; this
module is the single entry point the queue calls.
"""

from laya.config import github_action_filter_enabled
from laya.models.event import LayaEvent
from laya.pipeline.github_addressing import github_addressing


async def unaddressed_filter_rule(event: LayaEvent, space_id: str | None) -> str | None:
    """Return the filter rule text when an action filter drops ``event``, else None.

    None also covers platforms without a filter and platforms whose filter is off.
    """
    if github_action_filter_enabled(event.source.platform):
        addressing = await github_addressing(event, space_id)
    else:
        return None
    return None if addressing.addressed else addressing.filter_rule
