# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Static guards over the bundled n8n workflow templates.

Every node that calls the engine must send the n8n token, or its clone is
rejected once enforcement is on; the event POST nodes retry so a brief engine
restart does not lose a poll.
"""

import json
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from litellm import ModelResponse

from laya.integrations.n8n_bootstrap import WORKFLOWS_DIR
from laya.models.event import LayaEvent
from laya.pipeline.space_resolution import resolve_space
from laya.pipeline.stager import run_stager
from tests.conftest import insert_test_card, insert_test_event

TOKEN_HEADER = "X-Laya-N8n-Token"
TOKEN_VALUE = "={{ $env.LAYA_N8N_TOKEN }}"


def _templates() -> list[tuple[str, dict]]:
    return sorted(
        (f.name, json.loads(f.read_text(encoding="utf-8"))) for f in WORKFLOWS_DIR.glob("*.json")
    )


def _engine_nodes(data: dict) -> list[dict]:
    return [n for n in data.get("nodes", []) if "LAYA_ENGINE_URL" in json.dumps(n.get("parameters", {}))]


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_every_engine_calling_node_sends_the_n8n_token():
    missing = []
    seen = 0
    for name, data in _templates():
        for node in _engine_nodes(data):
            seen += 1
            params = node["parameters"]
            headers = (params.get("headerParameters") or {}).get("parameters") or []
            ok = params.get("sendHeaders") is True and any(
                h.get("name") == TOKEN_HEADER and h.get("value") == TOKEN_VALUE for h in headers
            )
            if not ok:
                missing.append(f"{name}: {node['name']}")
    assert seen >= 17, "expected the ingestion templates and the error handler to call the engine"
    assert not missing, f"nodes without the n8n token header: {missing}"


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_event_post_nodes_retry_on_failure():
    bad = []
    for name, data in _templates():
        for node in data.get("nodes", []):
            if node.get("name") != "POST to Laya Engine":
                continue
            if not (node.get("retryOnFail") is True and node.get("maxTries", 0) >= 2):
                bad.append(f"{name}: {node['name']}")
    assert not bad, f"POST nodes without retry: {bad}"


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_trello_failed_first_poll_keeps_the_provisioned_start():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the Trello poll")
    workflow = json.loads((WORKFLOWS_DIR / "trello-ingestion.json").read_text(encoding="utf-8"))
    code = next(n["parameters"]["jsCode"] for n in workflow["nodes"] if n["name"] == "Prepare Trello Poll")
    result = subprocess.run(
        [node, "-e", """
            const code = require('node:fs').readFileSync(0, 'utf8');
            const prepare = new Function('$input', '$getWorkflowStaticData', 'Date', code);
            const value = {boardId: 'AbCd1234', pollStartAt: '2026-11-22T16:00:00Z'};
            const input = {first: () => ({json: {value}})};
            function run(now, state) {
              class Clock extends Date {
                static now() { return Date.parse(now); }
              }
              return prepare(input, () => state, Clock)[0].json;
            }
            const attempts = [
              run('2026-11-22T16:02:00Z', {}),
              run('2026-11-22T16:04:00Z', {}),
              run('2026-11-22T16:06:00Z', {lastSuccessfulPoll: Date.parse('2026-11-22T16:04:00Z')}),
            ];
            process.stdout.write(JSON.stringify(attempts));
        """],
        input=code, text=True, capture_output=True, check=True, timeout=15,
    )
    first, retry, established = json.loads(result.stdout)
    assert retry["since"] == first["since"] == "2026-11-22T15:59:59.999Z"
    assert retry["since"] < "2026-11-22T16:00:10.000Z"
    assert established["since"] == "2026-11-22T16:03:58.999Z"


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_trello_poll_drains_bounded_windows_and_replays_failed_delivery():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the Trello poll")
    workflow = (WORKFLOWS_DIR / "trello-ingestion.json").read_text(encoding="utf-8")
    result = subprocess.run(
        [node, "-e", """
            const assert = require('node:assert/strict');
            const {nodes} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const parameters = name => nodes.find(node => node.name === name).parameters;
            const prepare = new Function('$input', '$getWorkflowStaticData', 'Date',
              parameters('Prepare Trello Poll').jsCode);
            const normalize = new Function('$input', '$workflow',
              parameters('Normalize Trello Actions').jsCode);
            const fetchQuery = parameters('Fetch Trello Actions').queryParameters.parameters;
            const start = Date.parse('2026-11-22T16:00:00Z');
            class Clock extends Date {
              static now() { return start + 11000; }
            }
            const value = {boardId: 'AbCd1234', pollStartAt: new Date(start).toISOString()};
            const input = json => ({first: () => ({json}), all: () => [{json}]});
            function queryFor(poll) {
              return Object.fromEntries(fetchQuery.map(({name, value}) => [
                name, value.startsWith('={{')
                  ? new Function('$json', 'return ' + value.slice(3, -2))(poll)
                  : value,
              ]));
            }
            const initial = prepare(input({value}), () => ({}), Clock)[0].json;
            assert.ok(queryFor(initial).limit <= 32, 'Trello request must bound response size');
            assert.ok(queryFor(initial).before, 'Trello request needs an upper bound');

            const select = new Function('$input', '$', parameters('Select Trello Window').jsCode);
            const save = new Function('$input', '$', '$getWorkflowStaticData',
              parameters('Save Successful Poll').jsCode);
            function action(index, offset) {
              return {
                id: index.toString(16).padStart(24, '0'), type: 'createCard',
                date: new Date(start + offset).toISOString(),
                data: {card: {id: 'card-' + index}},
              };
            }
            function run(state, actions, {fail = false} = {}) {
              const poll = prepare(input({value}), () => state, Clock)[0].json;
              const query = queryFor(poll);
              const response = actions.filter(action => {
                const at = Date.parse(action.date);
                if (at <= Date.parse(query.since)) return false;
                return actions.some(candidate => candidate.id === query.before)
                  ? action.id < query.before : at < Date.parse(query.before);
              }).sort((a, b) => b.id.localeCompare(a.id)).slice(0, query.limit);
              const selection = select(input({body: response}), () => input(poll))[0].json;
              const items = normalize(input(selection), {id: 'workflow'});
              const events = items.map(item => item.json).filter(event => event.event_id);
              if (!fail) save(input({}), () => input(selection), () => state);
              return {events, query};
            }

            const spaced = Array.from({length: 60}, (_, i) => action(i, i * 150));
            const state = {};
            const saturated = run(state, spaced);
            assert.equal(saturated.events.length, 0, 'Do not deliver the newest slice of a full window');
            let failed;
            for (let i = 0; i < 10; i++) {
              const beforeFailure = JSON.stringify(state);
              failed = run(state, spaced, {fail: true});
              assert.equal(JSON.stringify(state), beforeFailure, 'A delivery failure leaves the saved window intact');
              if (failed.events.length > 0) break;
              run(state, spaced);
            }
            assert.ok(failed.events.length > 0);
            const retried = run(state, spaced);
            assert.deepEqual(retried, failed, 'Retry reuses the same window and stable event IDs');

            const delivered = [...retried.events];
            for (let i = 0; i < 30 && delivered.length < spaced.length; i++) {
              delivered.push(...run(state, spaced).events);
            }
            assert.deepEqual(delivered.map(event => event.timestamp), spaced.map(action => action.date));

            const simultaneous = Array.from({length: 40}, (_, i) => action(i, 0));
            const denseState = {};
            const dense = new Set();
            for (let i = 0; i < 30 && dense.size < simultaneous.length; i++) {
              for (const event of run(denseState, simultaneous).events) {
                assert.ok(!dense.has(event.event_id), 'Action ID cursor made forward progress');
                dense.add(event.event_id);
              }
            }
            assert.equal(dense.size, simultaneous.length, 'Actions sharing one timestamp survive a full page');
        """],
        input=workflow, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def _normalize_trello_actions(workflow_id, pages):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the Trello normalizer")
    workflow = json.loads((WORKFLOWS_DIR / "trello-ingestion.json").read_text(encoding="utf-8"))
    code = next(n["parameters"]["jsCode"] for n in workflow["nodes"] if n["name"] == "Normalize Trello Actions")
    result = subprocess.run(
        [node, "-e", """
            const {code, workflowId, pages} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const run = new Function('$input', '$workflow', code);
            const items = pages.map(json => ({json}));
            process.stdout.write(JSON.stringify(run({all: () => items}, {id: workflowId})));
        """],
        input=json.dumps({"code": code, "workflowId": workflow_id, "pages": pages}),
        text=True, capture_output=True, check=True, timeout=15,
    )
    return [LayaEvent.model_validate(item["json"]) for item in json.loads(result.stdout)]


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_trello_fetches_card_creation_actions():
    workflow = json.loads((WORKFLOWS_DIR / "trello-ingestion.json").read_text(encoding="utf-8"))
    fetch = next(n for n in workflow["nodes"] if n["name"] == "Fetch Trello Actions")
    filters = next(
        param["value"] for param in fetch["parameters"]["queryParameters"]["parameters"]
        if param["name"] == "filter"
    ).split(",")
    assert {"createCard", "copyCard", "emailCard", "convertToCardFromCheckItem"} <= set(filters)


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
@pytest.mark.parametrize("action_type", ["copyCard", "emailCard", "convertToCardFromCheckItem"])
def test_trello_normalizes_additional_card_creation_actions(action_type):
    data = {
        "card": {"id": "new-card", "name": "New card", "shortLink": "AbCd1234"},
        "list": {"name": "Inbox"},
    }
    if action_type == "copyCard":
        data["cardSource"] = {"id": "original-card", "name": "Original card"}
    [event] = _normalize_trello_actions("test", [{"body": [{
        "id": "created", "type": action_type, "date": "2026-11-22T16:00:00Z", "data": data,
    }]}])
    assert event.source.raw_event_type == "card_created"
    assert event.entity_id == "trello:ticket:test:new-card"
    assert event.subject.title == "New card"
    assert event.subject.url == "https://trello.com/c/AbCd1234"
    assert event.content.metadata["trello_card_id"] == "new-card"
    assert event.content.body == "Card created in Inbox."


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_trello_normalizes_actions_across_pages_in_chronological_order():
    card = {"id": "card-id", "name": "Update onboarding", "shortLink": "AbCd1234"}
    board = {"id": "board-id", "name": "Work"}

    def action(action_type, minute, **data):
        return {
            "id": str(minute),
            "type": action_type,
            "date": f"2026-11-22T16:{minute:02}:00Z",
            "data": {"card": card, "board": board, **data},
            "memberCreator": {"fullName": "Board User", "username": "boarduser"},
        }

    assignment = action("addMemberToCard", 2)
    assignment["member"] = {"fullName": "Card Member", "username": "cardmember"}
    pages = [
        {"body": [
            action("updateCard", 6, old={"pos": 1}),
            action("updateCard", 5, old={"closed": False}, card={**card, "closed": True}),
            action("updateCard", 4, listBefore={"name": "To do"}, listAfter={"name": "Done"}),
            action("updateCard", 3, old={"dueComplete": False}, card={**card, "dueComplete": True}),
        ]},
        {"body": [assignment, action("commentCard", 1, text="Please review."), action("createCard", 0)]},
        {"body": []},
    ]
    events = _normalize_trello_actions("test", pages)

    assert [event.source.raw_event_type for event in events] == [
        "card_created", "card_commented", "card_member_added", "card_completed", "card_moved", "card_archived",
    ]
    assert {event.entity_id for event in events} == {"trello:ticket:test:card-id"}
    assert {event.subject.url for event in events} == {"https://trello.com/c/AbCd1234"}
    assert {event.source.connection_id for event in events} == {"test"}
    assert {event.content.metadata["trello_card_id"] for event in events} == {"card-id"}
    assert events[1].event_id == "evt_trello_test_1"
    assert events[1].actor.platform_handle == "boarduser"
    assert events[1].content.body == "Please review."
    assert events[2].content.metadata["participants"] == [
        {"role": "assignee", "name": "Card Member", "email": "", "handle": "cardmember"},
    ]
    assert events[4].content.body == "Card moved from To do to Done."


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
@pytest.mark.asyncio
async def test_trello_card_move_keeps_work_item_history_in_the_board_connection(
    db, sample_router_output_engineer,
):
    card = {"id": "card-id", "name": "Update onboarding", "shortLink": "AbCd1234"}
    for space_id in ("origin", "destination"):
        await db.execute(
            "INSERT INTO spaces (space_id, name, stager_model) VALUES (?, ?, ?)",
            (space_id, space_id, f"ollama/{space_id}"),
        )
        await db.execute(
            "INSERT INTO sources (source_id, name, platform, workflow_id, space_id) VALUES (?, ?, ?, ?, ?)",
            (f"src-{space_id}", space_id, "trello", f"workflow-{space_id}", space_id),
        )
    await db.commit()

    def action(action_id, action_type, minute, board):
        return {
            "id": action_id,
            "type": action_type,
            "date": f"2026-11-22T16:{minute:02}:00Z",
            "data": {"card": card, "board": {"id": board}},
        }

    created, moved_off = _normalize_trello_actions("workflow-origin", [{"body": [
        action("created", "createCard", 0, "origin-board"),
        action("moved-off", "moveCardFromBoard", 1, "origin-board"),
    ]}])
    moved_on, commented = _normalize_trello_actions("workflow-destination", [{"body": [
        action("moved-on", "moveCardToBoard", 1, "destination-board"),
        action("commented", "commentCard", 2, "destination-board"),
    ]}])

    async def event_space(event):
        await insert_test_event(
            db, event.event_id, platform="trello", raw_event_type=event.source.raw_event_type,
            subject_id=event.subject.id,
        )
        return await resolve_space(event.event_id, event.source.connection_id, event.source.platform)

    async def store_card(event, header, summary, space_id):
        await insert_test_card(
            db, card_id=f"card-{event.event_id}", event_id=event.event_id,
            entity_id=event.entity_id, status="ready", header=header, summary=summary,
            space_id=space_id,
        )
        await db.execute(
            "UPDATE action_cards SET created_at = ? WHERE event_id = ?",
            (event.timestamp.strftime("%Y-%m-%d %H:%M:%S"), event.event_id),
        )
        await db.commit()

    origin_space = await event_space(created)
    origin_header = "Origin board card title"
    origin_summary = "Origin board private details"
    await store_card(created, origin_header, origin_summary, origin_space)
    destination_space = await event_space(moved_on)
    moved_off_space = await event_space(moved_off)
    comment_space = await event_space(commented)

    reply = ModelResponse(
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        choices=[{
            "index": 0, "message": {"role": "assistant", "content": '{"header": "Update onboarding"}'},
            "finish_reason": "stop",
        }],
    )
    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=reply)) as complete,
        patch("laya.pipeline.stager.query_related_context", new=AsyncMock(return_value=[])),
        patch("laya.pipeline.budget.check_budget", new=AsyncMock()),
    ):
        await run_stager(moved_off, sample_router_output_engineer, space_id=moved_off_space)
        await run_stager(moved_on, sample_router_output_engineer, space_id=destination_space)
        await store_card(moved_on, "Destination board move", "Moved onto this board", destination_space)
        await run_stager(commented, sample_router_output_engineer, space_id=comment_space)

    assert [call.kwargs["model"] for call in complete.await_args_list] == [
        "ollama/origin", "ollama/destination", "ollama/destination",
    ]
    origin_prompt, move_prompt, comment_prompt = [
        call.kwargs["messages"][-1]["content"] for call in complete.await_args_list
    ]
    assert origin_header in origin_prompt
    assert origin_summary in origin_prompt
    assert origin_header not in move_prompt
    assert origin_summary not in move_prompt
    assert origin_header not in comment_prompt
    assert "Destination board move" in comment_prompt
