"""GitHub involvement facts: does this pull request or issue event concern the Laya user?

The GitHub ingestion workflow polls every recently updated issue, pull request and
conversation comment in each watched repository, so most events are about a
teammate's work the user has no part in: someone else's PR, a dependency bump, a bot
acknowledging another author's request. The facts below come from GitHub logins in
the event and in the stored history of the same pull request or issue, not from the
model's reading of names, and decide before any model call whether an event can need
the user.

An event involves the user when the user is the item's author, an assignee or a
requested reviewer, is @-mentioned by login, or appeared in one of those roles or
wrote on the item in an earlier event (a requested reviewer leaves the reviewer list
once the review is in, and the conversation after it still concerns them).

Comments with incomplete item participants still reach the model. The user's own
comments are filtered even if the item participants are unknown. The workflow
credits pull request and issue events to the item's author, so updates to the
user's own PR carry the user as actor regardless of who made the change.
"""

import re
from dataclasses import dataclass

from laya.config import get_self_user
from laya.db.sqlite import get_db
from laya.db.timeutil import db_ts
from laya.models.event import LayaEvent

# Participant roles that tie the user to the item. "commenter" is left out: as the
# user it is the user's own comment, which never counts as being addressed.
_INVOLVED_ROLES = frozenset({"author", "assignee", "reviewer"})

_COMMENT_EVENT_TYPE = "issue_comment_created"

FILTER_RULE_FROM_SELF = "GitHub comment was written by you"
FILTER_RULE_NOT_ADDRESSED = "GitHub item does not involve you"


@dataclass(frozen=True)
class GitHubAddressing:
    is_comment: bool
    author_is_self: bool
    user_is_author: bool
    user_is_assignee: bool
    user_is_reviewer: bool
    mentions_user: bool
    user_involved_before: bool
    item_participants_unknown: bool

    @property
    def involves_user(self) -> bool:
        return (
            self.user_is_author
            or self.user_is_assignee
            or self.user_is_reviewer
            or self.mentions_user
            or self.user_involved_before
        )

    @property
    def own_comment(self) -> bool:
        return self.is_comment and self.author_is_self

    @property
    def addressed(self) -> bool:
        return not self.own_comment and (self.involves_user or self.item_participants_unknown)

    @property
    def filter_rule(self) -> str:
        if self.own_comment:
            return FILTER_RULE_FROM_SELF
        return FILTER_RULE_NOT_ADDRESSED


def _mentions_user(body: str, logins: list[str]) -> bool:
    """True when ``body`` @-mentions one of the user's GitHub logins.

    The lookbehind rejects the middle of an address such as ``name@host``; the
    lookahead also excludes ``-`` because logins contain hyphens, so ``@ann`` does not
    match ``@ann-lee``.
    """
    return any(
        re.search(rf"(?<![\w@])@{re.escape(login)}(?![\w-])", body, re.IGNORECASE)
        for login in logins
    )


def _participant_roles(metadata: dict | None) -> list[dict]:
    participants = (metadata or {}).get("participants")
    if not isinstance(participants, list):
        return []
    return [p for p in participants if isinstance(p, dict)]


async def _user_involved_before(
    event: LayaEvent, space_id: str | None, handles: set[str],
) -> bool:
    if not handles:
        return False

    logins = ", ".join("?" for _ in handles)
    roles = ", ".join("?" for _ in _INVOLVED_ROLES)
    db = await get_db()
    # Older rows can contain invalid metadata or participant entries in formats
    # other than objects. Guard the JSON functions while searching those rows.
    previous = await db.execute_fetchall(
        f"""SELECT 1
            FROM events
            WHERE source_platform = 'github' AND source_connection_id IS ?
              AND subject_id = ?
              AND (space_id IS NULL OR space_id IS ?)
              AND (timestamp, event_id) < (?, ?)
              AND (
                LOWER(actor_handle) IN ({logins})
                OR EXISTS (
                    SELECT 1
                    FROM json_each(
                        CASE WHEN json_valid(events.content_metadata) THEN events.content_metadata END,
                        '$.participants'
                    ) AS participant
                    WHERE typeof(participant.key) = 'integer'
                      AND CASE WHEN participant.type = 'object' THEN
                          json_extract(participant.value, '$.role') IN ({roles})
                          AND json_type(participant.value, '$.handle') = 'text'
                          AND LOWER(json_extract(participant.value, '$.handle')) IN ({logins})
                      END
                )
              )
            LIMIT 1""",
        (
            event.source.connection_id, event.subject.id, space_id,
            db_ts(event.timestamp), event.event_id,
            *handles, *_INVOLVED_ROLES, *handles,
        ),
    )
    return bool(previous)


async def github_addressing(event: LayaEvent, space_id: str | None) -> GitHubAddressing:
    """Compute whether ``event`` (a GitHub PR, issue or comment event) involves the user."""
    user = get_self_user()
    if user is None:
        raise ValueError("GitHub action filtering requires a self profile in team settings")

    # Account entries mix platforms (GitHub logins, the Slack user id, the Jira account
    # id); a stray match on another platform's id inside GitHub data is harmless.
    logins = [a.strip() for a in user["accounts"] if a and a.strip()]
    handles = {login.lower() for login in logins}

    def is_user(handle: object) -> bool:
        return isinstance(handle, str) and handle.lower() in handles

    def user_roles(metadata: dict | None) -> set[str]:
        return {
            p.get("role")
            for p in _participant_roles(metadata)
            if p.get("role") in _INVOLVED_ROLES and is_user(p.get("handle"))
        }

    metadata = event.content.metadata
    roles = user_roles(metadata)
    is_comment = event.source.raw_event_type == _COMMENT_EVENT_TYPE
    item_participants_unknown = is_comment and (
        metadata.get("item_participants_complete") is False
        or not any(
            p.get("role") == "author"
            and isinstance(p.get("handle"), str)
            and p["handle"].strip()
            for p in _participant_roles(metadata)
        )
    )

    return GitHubAddressing(
        is_comment=is_comment,
        author_is_self=is_user(event.actor.platform_handle),
        user_is_author="author" in roles,
        user_is_assignee="assignee" in roles,
        user_is_reviewer="reviewer" in roles,
        mentions_user=_mentions_user(event.content.body or "", logins),
        user_involved_before=await _user_involved_before(event, space_id, handles),
        item_participants_unknown=item_participants_unknown,
    )
