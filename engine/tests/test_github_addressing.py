"""GitHub addressing: which pull request, issue and comment events concern the Laya user."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from laya.api.cards_lifecycle import reprocess_card
from laya.api.events import receive_event
from laya.api.settings_api import update_settings
from laya.api.team import get_team, update_team
from laya.config import load_settings
from laya.db.timeutil import db_ts
from laya.models.card import ActionCardData
from laya.models.classification import RouterOutput
from laya.models.event import LayaEvent
from laya.models.team import TeamConfig
from laya.pipeline import emit as emit_mod
from laya.pipeline.addressing import unaddressed_filter_rule
from laya.pipeline.github_addressing import (
    FILTER_RULE_FROM_SELF,
    FILTER_RULE_NOT_ADDRESSED,
    github_addressing,
)
from laya.pipeline.queue import _batch_route_events, process_event

USER = {
    "name": "Alex Morgan",
    "email": "alex@company.com",
    "emails": ["alex@company.com"],
    "accounts": ["alex-morgan", "U0ALEX"],
    "github_logins": ["alex-morgan"],
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
@pytest.mark.parametrize(
    ("event", "addressed"),
    [
        pytest.param(_pr_event("author", author="alex-morgan"), True, id="author"),
        pytest.param(
            _pr_event("assignee", participants=_people(assignees=["alex-morgan"])),
            True, id="assignee",
        ),
        pytest.param(
            _pr_event("reviewer", participants=_people(reviewers=["alex-morgan"])),
            True, id="reviewer",
        ),
        pytest.param(_comment_event("mention", body="cc @alex-morgan"), True, id="mention"),
        pytest.param(_comment_event("mine", commenter="alex-morgan"), False, id="own-comment"),
        pytest.param(_comment_event("unknown", participants=[]), True, id="incomplete-comment"),
    ],
)
async def test_direct_github_decisions_do_not_read_item_history(db, event, addressed):
    with patch("laya.pipeline.github_addressing._user_involved_before", return_value=False) as history:
        facts = await _facts(event)

    assert facts.addressed is addressed
    history.assert_not_awaited()


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
@pytest.mark.parametrize("match_by", ["actor", "reviewer"])
async def test_busy_item_history_finds_involvement_beyond_500_rows(db, match_by):
    unrelated_metadata = (
        "invalid JSON",
        json.dumps({"participants": ["old format", None, *_people("pat")]}),
    )
    await db.executemany(
        """INSERT INTO events (
               event_id, timestamp, source_platform, source_raw_event_type, actor_handle,
               subject_type, subject_id, content_metadata, raw_json
           ) VALUES (?, ?, 'github', 'issue_comment_created', 'pat',
                     'pull_request', ?, ?, '{}')""",
        [
            (f"unrelated-{i}", db_ts(BASE_TIME), ITEM, unrelated_metadata[i % 2])
            for i in range(501)
        ],
    )
    await db.commit()

    if match_by == "actor":
        await receive_event(_comment_event("mine", commenter="ALEX-MORGAN", minutes=1))
    else:
        await receive_event(
            _pr_event("reviewer", participants=_people(reviewers=["ALEX-MORGAN"]), minutes=-1)
        )
    facts = await _facts(_comment_event("reply", minutes=2))
    assert facts.user_involved_before
    assert facts.addressed


def _normalize_github_items(*items):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the bundled n8n normalizer")
    workflow = Path(__file__).resolve().parents[2] / "n8n/workflows/github-ingestion.json"
    result = subprocess.run(
        [
            node,
            "-e",
            """
            const fs = require('node:fs');
            const workflow = require(process.argv[1]);
            const items = JSON.parse(fs.readFileSync(0, 'utf8')).map(json => ({ json }));
            const step = workflow.nodes.find(node => node.name === 'Filter & Normalize');
            const normalize = new Function('$input', '$workflow', step.parameters.jsCode);
            const events = normalize({ all: () => items }, { id: 'github-workflow' });
            process.stdout.write(JSON.stringify(events.map(event => event.json)));
            """,
            str(workflow),
        ],
        input=json.dumps(items),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return [LayaEvent.model_validate(event) for event in json.loads(result.stdout)]


@pytest.mark.asyncio
@pytest.mark.parametrize("subject_type", ["ticket", "pull_request"])
async def test_first_poll_comment_keeps_item_participants(db, subject_type):
    repo_url = "https://api.github.com/repos/acme/api"
    issue = {
        "number": 42, "repository_url": repo_url, "title": "Refactor the cache",
        "user": {"login": "alex-morgan"}, "assignees": [{"login": "pat"}],
    }
    comment = {
        "id": 27, "issue_url": f"{repo_url}/issues/42",
        "created_at": BASE_TIME.isoformat(), "user": {"login": "sam-lee"},
    }
    details = []
    expected_participants = {
        ("author", "alex-morgan"), ("assignee", "pat"), ("commenter", "sam-lee"),
    }
    if subject_type == "pull_request":
        issue["pull_request"] = {}
        details.append({
            **issue, "base": {"repo": {"url": repo_url}},
            "requested_reviewers": [{"login": "kim"}],
        })
        expected_participants.add(("reviewer", "kim"))

    event, = _normalize_github_items(issue, comment, *details)
    assert event.source.raw_event_type == "issue_comment_created"
    assert event.subject.type == subject_type
    assert {
        (p["role"], p["handle"]) for p in event.content.metadata["participants"]
    } == expected_participants
    assert event.subject.title == issue["title"]
    assert (await _facts(event)).addressed


@pytest.mark.asyncio
async def test_comment_on_pr_with_missing_reviewers_is_preserved(db):
    repo_url = "https://api.github.com/repos/acme/api"
    issue = {
        "number": 42, "repository_url": repo_url, "pull_request": {},
        "user": {"login": "pat"}, "assignees": [],
    }
    comment = {
        "id": 27, "issue_url": f"{repo_url}/issues/42",
        "created_at": BASE_TIME.isoformat(), "user": {"login": "sam-lee"},
    }

    event, = _normalize_github_items(issue, comment)
    assert event.subject.type == "pull_request"
    assert event.content.metadata["item_participants_complete"] is False
    facts = await _facts(event)
    assert not facts.involves_user
    assert facts.addressed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commenter", "expected"),
    [("sam-lee", True), ("alex-morgan", False)],
)
async def test_legacy_comment_with_unknown_item_participants(db, commenter, expected):
    event = _comment_event(
        "legacy-comment", commenter=commenter, subject_type="ticket",
        participants=[{"role": "commenter", "handle": commenter, "name": commenter}],
    )
    facts = await _facts(event)
    assert facts.addressed is expected
    if not expected:
        assert facts.filter_rule == FILTER_RULE_FROM_SELF


@pytest.mark.asyncio
async def test_unrelated_history_does_not_count_as_involvement(db):
    await receive_event(_pr_event("elsewhere", author="alex-morgan", item="acme/api/7"))
    await receive_event(_comment_event("future", commenter="alex-morgan", minutes=9))
    await receive_event(_comment_event("same-time-later-id", commenter="alex-morgan", minutes=5))

    other_platform = _pr_event("other-platform", author="alex-morgan")
    other_platform.source.platform = "bitbucket"
    await receive_event(other_platform)
    other_connection = _pr_event("other-connection", author="alex-morgan")
    other_connection.source.connection_id = "different-connection"
    await receive_event(other_connection)
    await receive_event(_pr_event("other-space", author="alex-morgan"))
    await db.execute("UPDATE events SET space_id = 'default' WHERE event_id = 'other-space'")

    malformed = _pr_event("legacy-metadata")
    malformed.content.metadata["participants"] = {"0": _people("alex-morgan")[0]}
    await receive_event(malformed)
    facts = await _facts(_pr_event("now", minutes=5))
    assert not facts.user_involved_before
    assert not facts.addressed


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, {**USER, "github_logins": ["  "]}])
async def test_missing_github_identity_bypasses_filter(db, profile):
    with (
        patch("laya.config.load_settings", return_value=SETTINGS_ON),
        patch("laya.pipeline.github_addressing.get_self_user", return_value=profile),
    ):
        assert await unaddressed_filter_rule(_pr_event("e1"), None) is None


@pytest.fixture
def filter_settings_files(monkeypatch, tmp_path):
    monkeypatch.setattr("laya.config.LAYA_CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("laya.config.LAYA_TEAM_FILE", tmp_path / "team.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("accounts", [None, ["sam-lee", "U0ALEX"]])
async def test_enabling_github_filter_requires_identity(filter_settings_files, accounts):
    if accounts is not None:
        await update_team(TeamConfig(members=[{
            "name": USER["name"], "email": USER["email"], "role": "self", "accounts": accounts,
        }]))

    with pytest.raises(HTTPException) as exc:
        await update_settings(SETTINGS_ON)
    assert exc.value.status_code == 422
    assert "GitHub login" in exc.value.detail
    assert load_settings()["pipeline"]["github_actionable_only"] is False


@pytest.mark.asyncio
async def test_untyped_account_alias_does_not_mark_teammate_comment_as_own(
    db, filter_settings_files,
):
    await update_team(TeamConfig(members=[{
        "name": USER["name"], "email": USER["email"], "role": "self",
        "accounts": ["sam-lee"], "github_logins": [" Alex-Morgan "],
    }]))
    assert (await update_settings(SETTINGS_ON))["status"] == "updated"

    event = _comment_event(
        "alias-collision", commenter="sam-lee", body="Please check, @alex-morgan.",
        participants=_people("alex-morgan", commenter="sam-lee"),
    )
    assert await unaddressed_filter_rule(event, None) is None


@pytest.mark.asyncio
async def test_team_reports_when_removing_identity_pauses_github_filter(filter_settings_files):
    profile = TeamConfig(members=[{
        "name": USER["name"], "email": USER["email"], "role": "self",
        "accounts": USER["accounts"], "github_logins": USER["github_logins"],
    }])
    await update_team(profile)
    assert (await update_settings(SETTINGS_ON))["status"] == "updated"
    assert "github_filter_warning" not in await get_team()

    profile_without_logins = profile.model_copy(deep=True)
    profile_without_logins.members[0].github_logins.clear()
    removal = await update_team(profile_without_logins)
    assert "GitHub login" in removal["github_filter_warning"]
    assert (await get_team())["github_filter_warning"] == removal["github_filter_warning"]

    await update_team(profile)
    assert "github_filter_warning" not in await get_team()


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


@pytest.mark.asyncio
async def test_batch_router_only_receives_addressed_github_events(db):
    events = [
        _pr_event("assigned", item="acme/api/7", participants=_people(assignees=["alex-morgan"])),
        _pr_event("unrelated", item="acme/api/8"),
        _comment_event("mentioned", item="acme/api/9", body="cc @alex-morgan"),
    ]
    for event in events:
        await receive_event(event)
    classification = RouterOutput(
        category="CODE", persona="ENGINEER", priority="LOW", confidence=0.9,
    )

    with (
        patch("laya.config.load_settings", return_value=SETTINGS_ON),
        patch("laya.pipeline.github_addressing.get_self_user", return_value=USER),
        patch("laya.pipeline.queue._batch_router_cache", {}),
        patch("laya.pipeline.router.run_batch_router") as route,
    ):
        route.side_effect = lambda batch: {entry["event_id"]: classification for entry in batch}
        await _batch_route_events([event.event_id for event in events])

    route.assert_awaited_once()
    batch, = route.await_args.args
    assert [entry["event_id"] for entry in batch] == ["assigned", "mentioned"]


@pytest.mark.asyncio
async def test_reprocess_keeps_existing_github_card_after_filter_is_enabled(db):
    event = _pr_event("gh-reprocess")
    await receive_event(event)
    settings = {"pipeline": {"github_actionable_only": False}}
    classification = RouterOutput(
        category="CODE", persona="ENGINEER", priority="LOW", confidence=0.9,
    )
    draft = ActionCardData(
        header="Original header", summary="PR summary",
        staged_output={"type": "text", "content": "PR details"},
    )

    with (
        patch("laya.config.load_settings", return_value=settings),
        patch("laya.pipeline.github_addressing.get_self_user", return_value=USER),
        patch("laya.pipeline.router.run_router", return_value=classification) as route,
        patch("laya.pipeline.stager.run_stager", return_value=draft),
        patch("laya.pipeline.emit._embed_card", return_value=""),
        patch("laya.pipeline.emit._resolve_context_grouping", return_value=None),
        patch("laya.pipeline.emit._trigger_followups"),
    ):
        await process_event(event.event_id)
        original, = await db.execute_fetchall(
            "SELECT card_id, status FROM action_cards WHERE event_id = ?", (event.event_id,),
        )
        assert original["status"] == "ready"

        settings["pipeline"]["github_actionable_only"] = True
        assert await reprocess_card(original["card_id"]) == {
            "status": "reprocessing", "card_id": original["card_id"],
        }
        # A restarted engine resumes without the in-memory reprocess marker.
        emit_mod._reprocess_event_ids.discard(event.event_id)
        draft.header = "Updated header"
        await process_event(event.event_id)

    stored_event, = await db.execute_fetchall(
        "SELECT processing_status, filtered FROM events WHERE event_id = ?", (event.event_id,),
    )
    assert stored_event["processing_status"] == "completed"
    assert not stored_event["filtered"]
    updated, = await db.execute_fetchall(
        "SELECT card_id, status, header FROM action_cards WHERE event_id = ?", (event.event_id,),
    )
    assert updated["card_id"] == original["card_id"]
    assert updated["status"] == "ready"
    assert updated["header"] == "Updated header"
    assert route.await_count == 2
