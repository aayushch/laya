# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from laya.api.events import receive_event
from laya.models.card import ActionCardData, StagedOutput
from laya.models.event import LayaEvent
from laya.pipeline.queue import _batch_route_events, process_event
from laya.pipeline.source_completion import source_completion_reason
from tests.conftest import insert_test_card

BASE_TIME = datetime(2026, 11, 22, 15, tzinfo=timezone.utc)


def make_event(
    event_id, *, platform="jira", raw_type="issue_created", subject_type=None,
    subject_id="SC-100", connection_id="workflow-1", minutes=0, metadata=None,
):
    return LayaEvent(
        event_id=event_id,
        timestamp=BASE_TIME + timedelta(minutes=minutes),
        source={"platform": platform, "connection_id": connection_id, "raw_event_type": raw_type},
        actor={"name": "Me", "email": "me@example.com", "platform_handle": "me"},
        subject={"type": subject_type or ("pull_request" if platform == "github" else "ticket"),
                 "id": subject_id, "title": "Example work item"},
        content={"body": "Example activity", "metadata": metadata or {}},
    )


@pytest.fixture
def source_completion():
    with patch("laya.pipeline.source_completion.load_settings", return_value={
        "pipeline": {"source_card_completion": True},
    }), patch("laya.config.load_rules", return_value={"rules": []}):
        yield


async def add_card(db, event, *, status="ready", space_id="default"):
    await receive_event(event)
    await insert_test_card(
        db, card_id=event.event_id, event_id=event.event_id, status=status, space_id=space_id,
        entity_id=event.entity_id,
    )


def jira_done(event_id="done"):
    return make_event(
        event_id, minutes=1, raw_type="issue_resolved",
        metadata={"jira_previous_status": "In Progress", "jira_new_status": "Done", "jira_status": "Done"},
    )


@pytest.mark.parametrize(
    "event",
    [
        make_event("pr-closed", platform="github", raw_type="pull_request_closed"),
        make_event("jira-comment", raw_type="issue_commented", metadata={"jira_status": "Done"}),
        make_event("jira-cancelled", raw_type="issue_resolved", metadata={"jira_new_status": "Cancelled"}),
        make_event("jira-reopened", raw_type="issue_resolved",
                   metadata={"jira_new_status": "Done", "jira_status": "In Progress"}),
        make_event("missing-key", subject_id="", raw_type="issue_resolved",
                   metadata={"jira_new_status": "Done"}),
    ],
)
def test_non_completion_events(event):
    assert source_completion_reason(event) is None


@pytest.mark.asyncio
async def test_jira_done_completes_earlier_card_without_routing(db, source_completion):
    await add_card(db, make_event("earlier"))
    await receive_event(jira_done())

    with (
        patch("laya.pipeline.router.run_router", new_callable=AsyncMock) as route,
        patch("laya.models.card_lifecycle.manager.broadcast", new_callable=AsyncMock) as broadcast,
    ):
        await process_event("done")
        await process_event("done")

    route.assert_not_awaited()
    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[0]["payload"]["status"] == "done"
    cards = await db.execute_fetchall(
        "SELECT event_id, status, status_set_by, status_reason, resolved_at FROM action_cards"
    )
    [card] = cards
    assert tuple(card)[:4] == ("earlier", "done", "laya", "Jira issue moved to Done.")
    assert card["resolved_at"]
    event = await db.execute_fetchall("SELECT processing_status FROM events WHERE event_id = 'done'")
    assert event[0]["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_github_merge_closes_only_ready_cards_from_the_same_source(db, source_completion):
    repo_pr = {"platform": "github", "subject_id": "acme/backend/123"}
    for card_id, overrides, status, space in [
        ("pr", {}, "ready", "default"),
        ("comment", {"subject_type": "ticket"}, "ready", "default"),
        ("archived", {}, "archived", "default"),
        ("agent", {}, "agent_running", "default"),
        ("later", {"minutes": 2}, "ready", "default"),
        ("reopened", {}, "ready", "default"),
        ("other-pr", {"subject_id": "acme/frontend/123"}, "ready", "default"),
        ("other-connection", {"connection_id": "workflow-2"}, "ready", "default"),
        ("other-space", {}, "ready", "another-space"),
    ]:
        await add_card(db, make_event(card_id, **(repo_pr | overrides)), status=status, space_id=space)
    await db.execute("UPDATE action_cards SET status_set_by = 'user' WHERE card_id = 'reopened'")
    await db.commit()
    await receive_event(make_event("merge", minutes=1, raw_type="pull_request_merged", **repo_pr))

    with patch("laya.pipeline.router.run_router", new_callable=AsyncMock) as route:
        await process_event("merge")
    route.assert_not_awaited()

    rows = await db.execute_fetchall("SELECT card_id, status FROM action_cards")
    assert {row["card_id"]: row["status"] for row in rows} == {
        "pr": "done", "comment": "done", "archived": "archived", "agent": "agent_running",
        "later": "ready", "reopened": "ready", "other-pr": "ready",
        "other-connection": "ready", "other-space": "ready",
    }


@pytest.mark.asyncio
async def test_older_card_emitted_after_completion_is_done(db, source_completion, sample_router_output_comms):
    await receive_event(jira_done())
    await process_event("done")
    earlier = make_event("delayed")
    await receive_event(earlier)
    stage = ActionCardData(
        header="Older update", summary="Older activity on the issue.",
        staged_output=StagedOutput(type="summary", content="Older activity"),
    )

    with (
        patch("laya.pipeline.router.run_router", new_callable=AsyncMock,
              return_value=sample_router_output_comms),
        patch("laya.pipeline.stager.run_stager", new_callable=AsyncMock, return_value=stage),
        patch("laya.pipeline.emit.embed_document", new_callable=AsyncMock),
        patch("laya.pipeline.emit.resolve_semantic_entities", new_callable=AsyncMock),
        patch("laya.pipeline.emit.trigger_summary_update", new_callable=AsyncMock),
    ):
        await process_event(earlier.event_id)
    cards = await db.execute_fetchall("SELECT event_id, status, status_reason FROM action_cards")
    assert [tuple(row) for row in cards] == [("delayed", "done", "Jira issue moved to Done.")]


@pytest.mark.asyncio
async def test_batch_router_excludes_completions(db, source_completion):
    events = [make_event("one"), jira_done(), make_event("two")]
    for event in events:
        await receive_event(event)

    with (
        patch("laya.pipeline.router.run_batch_router", new_callable=AsyncMock,
              return_value={}) as route,
        patch("laya.pipeline.queue._trip_batch_breaker"),
    ):
        await _batch_route_events([event.event_id for event in events])

    route.assert_awaited_once()
    assert [entry["event_id"] for entry in route.await_args.args[0]] == ["one", "two"]


@pytest.mark.asyncio
async def test_completion_is_opt_in(db, sample_router_output_comms):
    await add_card(db, make_event("earlier"))
    await receive_event(jira_done())

    with (
        patch("laya.pipeline.source_completion.load_settings", return_value={"pipeline": {}}),
        patch("laya.config.load_rules", return_value={"rules": []}),
        patch("laya.pipeline.router.run_router", new_callable=AsyncMock,
              return_value=sample_router_output_comms) as route,
        patch("laya.pipeline.queue._run_simple_pipeline", new_callable=AsyncMock) as stage,
    ):
        await process_event("done")

    route.assert_awaited_once()
    stage.assert_awaited_once()
    [card] = await db.execute_fetchall("SELECT status FROM action_cards")
    assert card["status"] == "ready"


@pytest.mark.asyncio
async def test_completion_respects_filter_rules(db, source_completion):
    await add_card(db, make_event("earlier"))
    await receive_event(jira_done())

    with (
        patch("laya.pipeline.rules.run_rules", new_callable=AsyncMock, return_value=(True, "Ignore")),
        patch("laya.pipeline.router.run_router", new_callable=AsyncMock) as route,
    ):
        await process_event("done")

    route.assert_not_awaited()
    [card] = await db.execute_fetchall("SELECT status FROM action_cards")
    assert card["status"] == "ready"
    [event] = await db.execute_fetchall("SELECT processing_status FROM events WHERE event_id = 'done'")
    assert event["processing_status"] == "filtered"

@pytest.mark.asyncio
async def test_completion_keeps_newer_cards_within_the_same_second(db, source_completion):
    completion = jira_done()
    completion.timestamp = completion.timestamp.replace(microsecond=100_200)
    for event_id, microsecond in [("before", 100_100), ("at", 100_200), ("after", 100_300)]:
        event = make_event(event_id, minutes=1)
        # The raw payload can use an offset while the indexed column uses UTC.
        event.timestamp = event.timestamp.replace(microsecond=microsecond).astimezone(
            timezone(timedelta(hours=2)),
        )
        await add_card(db, event)
    await receive_event(completion)
    await process_event(completion.event_id)

    rows = await db.execute_fetchall("SELECT event_id, status FROM action_cards")
    assert dict(map(tuple, rows)) == {"before": "done", "at": "done", "after": "ready"}


@pytest.mark.asyncio
@pytest.mark.parametrize("microsecond, expected", [(100_100, "done"), (100_200, "done"), (100_300, "ready")])
async def test_late_emit_uses_the_full_source_cutoff(db, source_completion, microsecond, expected):
    from laya.pipeline.card_status import StatusDecision
    from laya.pipeline.source_completion import apply_late_source_completion

    completion = jira_done()
    completion.timestamp = completion.timestamp.replace(microsecond=100_200).astimezone(
        timezone(timedelta(hours=2)),
    )
    await receive_event(completion)
    await process_event(completion.event_id)

    event = make_event("delayed", minutes=1)
    event.timestamp = event.timestamp.replace(microsecond=microsecond, tzinfo=None)
    decision = StatusDecision(status="ready")
    await apply_late_source_completion(db, event, "default", decision)
    assert decision.status == expected
