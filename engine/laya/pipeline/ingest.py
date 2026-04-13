"""INGEST pipeline step — resolve actor relationship from team.json."""

import structlog

from laya.config import load_team
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent
from laya.models.team import TeamConfig

log = structlog.get_logger()


async def resolve_actor_relationship(event: LayaEvent) -> str:
    """Look up actor in team.json by email, aliases, or platform accounts.

    Returns "external" if no match is found.
    """
    team_data = load_team()
    team = TeamConfig(**team_data)

    actor_email = event.actor.email.lower().strip()
    actor_handle = (event.actor.platform_handle or "").lower().strip()

    for member in team.members:
        # Match on primary email (skip if actor email is empty — e.g. Bitbucket/GitHub webhooks)
        if actor_email and member.email.lower() == actor_email:
            log.debug("actor_resolved", email=actor_email, role=member.role.value, name=member.name)
            return member.role.value

        # Match on alias emails
        if actor_email and any(alias.lower() == actor_email for alias in member.aliases):
            log.debug("actor_resolved_alias", email=actor_email, role=member.role.value, name=member.name)
            return member.role.value

        # Match on platform account names (e.g., GitHub username "jdoe")
        if actor_handle and any(acc.lower() == actor_handle for acc in member.accounts):
            log.debug("actor_resolved_account", handle=actor_handle, role=member.role.value, name=member.name)
            return member.role.value

    # Fallback: match on exact display name (case-insensitive).
    # This covers platforms like Bitbucket/GitHub where webhooks provide
    # display_name but not email. Lower confidence than email/handle but
    # far better than defaulting to "external" when the name matches.
    actor_name = event.actor.name.strip().lower()
    if len(actor_name) >= 2:
        for member in team.members:
            if member.name.strip().lower() == actor_name:
                log.debug(
                    "actor_resolved_name_fallback",
                    name=actor_name,
                    role=member.role.value,
                    member_name=member.name,
                )
                return member.role.value

    log.debug("actor_unresolved", email=actor_email, name=event.actor.name, defaulting_to="external")
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
