"""INGEST pipeline step — resolve actor relationship from team.json."""

from __future__ import annotations

from typing import Any

import structlog

from laya.config import load_team
from laya.db.sqlite import get_db
from laya.models.event import LayaEvent
from laya.models.team import TeamConfig, TeamMember

log = structlog.get_logger()


def _match_team_member(
    team: TeamConfig,
    *,
    email: str = "",
    handle: str = "",
    name: str = "",
) -> TeamMember | None:
    """Match an identity against team.json members.

    Tries: primary email → alias email → platform handle → display name.
    Returns the matched TeamMember or None.
    """
    email = email.lower().strip()
    handle = handle.lower().strip()

    for member in team.members:
        if email and member.email.lower() == email:
            return member
        if email and any(alias.lower() == email for alias in member.aliases):
            return member
        if handle and any(acc.lower() == handle for acc in member.accounts):
            return member

    # Fallback: display name (lower confidence)
    name = name.strip().lower()
    if len(name) >= 2:
        for member in team.members:
            if member.name.strip().lower() == name:
                return member

    return None


def resolve_participant_roles(
    event: LayaEvent,
    team: TeamConfig,
    actor_relationship: str,
) -> dict[str, Any]:
    """Resolve participant roles from event metadata.

    Returns a dict with:
      - laya_user_role: the role the Laya user plays (e.g. "reviewer", "assignee") or None
      - actor_role: the role the event actor plays (e.g. "author", "commenter") or None
      - participants: enriched list with 'relationship' added to each participant
    """
    metadata = event.content.metadata
    raw_participants: list[dict] = metadata.get("participants", [])

    if not raw_participants:
        return {"laya_user_role": None, "actor_role": None, "participants": []}

    actor_email = event.actor.email.lower().strip()
    actor_handle = (event.actor.platform_handle or "").lower().strip()
    actor_name = event.actor.name.strip().lower()

    laya_user_role: str | None = None
    actor_role: str | None = None
    enriched: list[dict] = []

    for p in raw_participants:
        role = p.get("role", "")
        p_email = (p.get("email") or "").lower().strip()
        p_handle = (p.get("handle") or "").lower().strip()
        p_name = (p.get("name") or "").strip()

        # Resolve this participant against team.json
        member = _match_team_member(team, email=p_email, handle=p_handle, name=p_name)
        relationship = member.role.value if member else "external"

        enriched.append({
            "role": role,
            "name": p_name,
            "email": p_email,
            "handle": p_handle,
            "relationship": relationship,
        })

        # Is this participant the Laya user?
        if relationship == "self":
            laya_user_role = role

        # Is this participant the event actor?
        if actor_email and p_email and p_email == actor_email:
            actor_role = role
        elif actor_handle and p_handle and p_handle == actor_handle:
            actor_role = role
        elif not actor_role and actor_name and p_name.lower() == actor_name:
            actor_role = role

    log.debug(
        "participant_roles_resolved",
        event_id=event.event_id,
        laya_user_role=laya_user_role,
        actor_role=actor_role,
        participant_count=len(enriched),
    )

    return {
        "laya_user_role": laya_user_role,
        "actor_role": actor_role,
        "participants": enriched,
    }


async def resolve_actor_relationship(event: LayaEvent) -> str:
    """Look up actor in team.json by email, aliases, or platform accounts.

    Returns "external" if no match is found.
    """
    team_data = load_team()
    team = TeamConfig(**team_data)

    member = _match_team_member(
        team,
        email=event.actor.email,
        handle=event.actor.platform_handle or "",
        name=event.actor.name,
    )

    if member:
        log.debug("actor_resolved", role=member.role.value, name=member.name)
        return member.role.value

    log.debug("actor_unresolved", email=event.actor.email, name=event.actor.name, defaulting_to="external")
    return "external"


async def run_ingest(event: LayaEvent) -> tuple[str, dict[str, Any]]:
    """Run the INGEST step: resolve actor and participant roles.

    Returns (actor_relationship, participant_roles) where participant_roles
    contains laya_user_role, actor_role, and enriched participants list.
    """
    db = await get_db()

    team_data = load_team()
    team = TeamConfig(**team_data)

    # Resolve actor relationship (who is the actor relative to the Laya user?)
    member = _match_team_member(
        team,
        email=event.actor.email,
        handle=event.actor.platform_handle or "",
        name=event.actor.name,
    )
    relationship = member.role.value if member else "external"

    # Resolve participant roles (what role does the Laya user play in this context?)
    participant_roles = resolve_participant_roles(event, team, relationship)

    await db.execute(
        "UPDATE events SET actor_relationship = ? WHERE event_id = ?",
        (relationship, event.event_id),
    )
    await db.commit()

    log.info(
        "ingest_complete",
        event_id=event.event_id,
        actor_relationship=relationship,
        laya_user_role=participant_roles["laya_user_role"],
        actor_role=participant_roles["actor_role"],
    )
    return relationship, participant_roles
