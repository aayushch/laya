"""INGEST pipeline step — resolve actor relationship from team.json."""

import structlog

from laya.config import load_team
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent
from laya.models.team import TeamConfig

log = structlog.get_logger()


async def resolve_actor_relationship(event: LayaEvent) -> str:
    """Look up actor.email in team.json and return the relationship role.

    Returns "external" if the email is not found.
    """
    team_data = load_team()
    team = TeamConfig(**team_data)

    actor_email = event.actor.email.lower()
    for member in team.members:
        if member.email.lower() == actor_email:
            log.debug("actor_resolved", email=actor_email, role=member.role.value, name=member.name)
            return member.role.value

    log.debug("actor_unresolved", email=actor_email, defaulting_to="external")
    return "external"


async def run_ingest(event: LayaEvent) -> str:
    """Run the INGEST step: resolve actor and update the event row.

    Returns the resolved actor_relationship string.
    """
    db = await get_db()
    relationship = await resolve_actor_relationship(event)

    await db.execute(
        "UPDATE events SET actor_relationship = ? WHERE event_id = ?",
        (relationship, event.event_id),
    )
    await db.commit()

    log.info("ingest_complete", event_id=event.event_id, actor_relationship=relationship)
    return relationship
