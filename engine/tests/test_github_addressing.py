"""GitHub addressing: which pull request, issue and comment events concern the Laya user."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from laya.api.events import receive_event
from laya.models.event import LayaEvent
from laya.pipeline.addressing import unaddressed_filter_rule
from laya.pipeline.github_addressing import (
    FILTER_RULE_FROM_SELF,
    FILTER_RULE_NOT_ADDRESSED,
    github_addressing,
)
from laya.pipeline.queue import process_event

USER = {
    "name": "Alex Morgan",
    "email": "alex@company.com",
    "emails": ["alex@company.com"],
    "accounts": ["alex-morgan", "U0ALEX"],
}
BASE_TIME = datetime(2026, 2, 22, 14, 30, 0, tzinfo=timezone.utc)
SETTINGS_ON = {"pipeline": {"github_actionable_only": True}}
ITEM = "acme/api/42"


def _people(author="sam-lee", assignees=(), reviewers=(), commenter=None):
    people = [{"role": "author", "name": author, "handle": author}]
    people += [{"role": "assignee", "name": a, "handle": a} for a in assignees]
    people += [{"role": "reviewer", "name": r, "handle": r} for r in reviewers]
    if commenter:
        people.append({"role": "commenter", "name": commenter, "handle": commenter})
    return people


def _pr_event(event_id, *, author="sam-lee", body="Refactor the cache.", item=ITEM,
              participants=None, minutes=0, state="open", is_pr=True):
    kind = "pull_request" if is_pr else "issue"
    return LayaEvent(
        event_id=event_id,
        timestamp=BASE_TIME + timedelta(minutes=minutes),
        source={
            "platform": "github",
            "raw_event_type": f"{kind}_closed" if state == "closed" else f"{kind}_updated",
        },
        # The workflow credits PR and issue events to the item's author.
        actor={"name": author, "email": "", "platform_handle": author},
        subject={"type": "pull_request" if is_pr else "ticket", "id": item, "title": "Refactor the cache"},
        content={
            "body": body,
            "attachments": [],
            "metadata": {
                "state": state,
                "is_pr": is_pr,
                "participants": participants if participants is not None else _people(author),
            },
        },
    )


def _comment_event(event_id, *, commenter="sam-lee", body="Looks good.", item=ITEM,
                   participants=None, minutes=0, subject_type="pull_request"):
    return LayaEvent(
        event_id=event_id,
        timestamp=BASE_TIME + timedelta(minutes=minutes),
        source={"platform": "github", "raw_event_type": "issue_comment_created"},
        actor={"name": commenter, "email": "", "platform_handle": commenter},
        subject={"type": subject_type, "id": item, "title": "Refactor the cache"},
        content={
            "body": body,
            "attachments": [],
            "metadata": {
                "is_comment": True,
                "participants": (
                    participants if participants is not None
                    else _people(commenter=commenter)
                ),
            },
        },
    )


async def _facts(event):
    with patch("laya.pipeline.github_addressing.get_self_user", return_value=USER):
        return await github_addressing(event, None)


@pytest.mark.asyncio
async def test_teammates_pr_the_user_is_not_on_is_not_addressed(db):
    facts = await _facts(_pr_event("e1"))
    assert not facts.addressed
    assert facts.filter_rule == FILTER_RULE_NOT_ADDRESSED


@pytest.mark.asyncio
async def test_bot_acknowledging_another_author_is_not_addressed(db):
    event = _comment_event(
        "e1", commenter="github-actions[bot]",
        body="Hi @sam-lee, I've received your request, and I'm working on it now!",
        participants=_people("sam-lee", commenter="github-actions[bot]"),
    )
    facts = await _facts(event)
    assert not facts.addressed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "participants",
    [
        _people("Alex-Morgan"),
        _people("sam-lee", assignees=["pat", "alex-morgan"]),
        # The user's entry is not the last of several reviewers.
        _people("sam-lee", reviewers=["alex-morgan", "pat", "kim"]),
    ],
)
async def test_author_assignee_or_requested_reviewer_is_addressed(db, participants):
    facts = await _facts(_pr_event("e1", author=participants[0]["handle"], participants=participants))
    assert facts.addressed


@pytest.mark.asyncio
async def test_update_to_the_users_own_pr_is_addressed(db):
    # A push or merge on the user's PR arrives credited to the user, its author.
    facts = await _facts(_pr_event("e1", author="alex-morgan", state="closed"))
    assert facts.author_is_self
    assert facts.addressed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("@alex-morgan can you take a look?", True),
        ("cc @ALEX-MORGAN", True),
        ("thanks (@alex-morgan)", True),
        ("@alex-morgan-bot ran the checks", False),
        ("@alex-morganx please review", False),
        ("mail alex@alex-morgan.dev", False),
        ("alex-morgan wrote this", False),
    ],
)
async def test_mention_detection(db, body, expected):
    facts = await _facts(_comment_event("e1", body=body))
    assert facts.mentions_user is expected
    assert facts.addressed is expected


@pytest.mark.asyncio
async def test_users_own_comment_is_never_addressed(db):
    event = _comment_event(
        "e1", commenter="alex-morgan", body="Pushed a fix, @alex-morgan reminder to self.",
        participants=_people("alex-morgan", commenter="alex-morgan"),
    )
    facts = await _facts(event)
    assert facts.own_comment
    assert not facts.addressed
    assert facts.filter_rule == FILTER_RULE_FROM_SELF


@pytest.mark.asyncio
async def test_comment_on_the_users_pr_is_addressed(db):
    event = _comment_event(
        "e1", commenter="sam-lee", participants=_people("alex-morgan", commenter="sam-lee"),
    )
    facts = await _facts(event)
    assert facts.user_is_author
    assert facts.addressed


@pytest.mark.asyncio
async def test_reply_after_the_users_comment_is_addressed(db):
    # The user's own comment is stored (filtered) and still marks them as involved.
    await receive_event(_comment_event("mine", commenter="alex-morgan", minutes=0))
    facts = await _facts(_comment_event("reply", commenter="sam-lee", minutes=5))
    assert facts.user_involved_before
    assert facts.addressed


@pytest.mark.asyncio
async def test_former_reviewer_stays_involved(db):
    # GitHub drops a reviewer from the requested list once the review is submitted.
    await receive_event(_pr_event("requested", participants=_people(reviewers=["alex-morgan"])))
    facts = await _facts(_pr_event("after-review", minutes=10))
    assert facts.user_involved_before
    assert facts.addressed


@pytest.mark.asyncio
async def test_comment_on_an_issue_the_user_filed_is_addressed(db):
    # A comment on a plain issue lists only its commenter; the earlier issue event
    # carries the author.
    await receive_event(_pr_event("issue", author="alex-morgan", is_pr=False, minutes=0))
    facts = await _facts(_comment_event("c1", subject_type="ticket", minutes=3))
    assert facts.addressed


@pytest.mark.asyncio
async def test_involvement_on_other_items_and_later_events_does_not_count(db):
    await receive_event(_pr_event("elsewhere", author="alex-morgan", item="acme/api/7"))
    await receive_event(_comment_event("future", commenter="alex-morgan", minutes=9))
    facts = await _facts(_pr_event("now", minutes=5))
    assert not facts.user_involved_before
    assert not facts.addressed


@pytest.mark.asyncio
async def test_missing_self_profile_is_an_error(db):
    with patch("laya.pipeline.github_addressing.get_self_user", return_value=None):
        with pytest.raises(ValueError, match="self profile"):
            await github_addressing(_pr_event("e1"), None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "expected"),
    [(SETTINGS_ON, FILTER_RULE_NOT_ADDRESSED), ({"pipeline": {}}, None)],
)
async def test_dispatcher_follows_the_setting(db, settings, expected):
    with (
        patch("laya.config.load_settings", return_value=settings),
        patch("laya.pipeline.github_addressing.get_self_user", return_value=USER),
    ):
        assert await unaddressed_filter_rule(_pr_event("e1"), None) == expected


@pytest.mark.asyncio
async def test_dispatcher_ignores_other_platforms(db, slack_event):
    with patch("laya.config.load_settings", return_value=SETTINGS_ON):
        assert await unaddressed_filter_rule(slack_event, None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "rule"),
    [
        (_pr_event("gh-1"), FILTER_RULE_NOT_ADDRESSED),
        (
            _comment_event("gh-2", commenter="alex-morgan",
                           participants=_people("alex-morgan", commenter="alex-morgan")),
            FILTER_RULE_FROM_SELF,
        ),
    ],
)
async def test_queue_filters_uninvolved_github_without_a_model_call(db, event, rule):
    await receive_event(event)

    with (
        patch("laya.config.load_settings", return_value=SETTINGS_ON),
        patch("laya.pipeline.github_addressing.get_self_user", return_value=USER),
        patch("laya.pipeline.router.llm_call", new_callable=AsyncMock) as route,
        patch("laya.pipeline.queue._run_simple_pipeline", new_callable=AsyncMock) as stage,
    ):
        await process_event(event.event_id)

    rows = await db.execute_fetchall(
        "SELECT processing_status, filtered, filter_rule FROM events WHERE event_id = ?",
        (event.event_id,),
    )
    assert rows[0]["processing_status"] == "filtered"
    assert rows[0]["filtered"] == 1
    assert rows[0]["filter_rule"] == rule
    route.assert_not_awaited()
    stage.assert_not_awaited()
