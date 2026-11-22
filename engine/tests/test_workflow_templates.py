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

import pytest

from laya.integrations.n8n_bootstrap import WORKFLOWS_DIR
from laya.models.event import LayaEvent

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
def test_trello_normalizes_actions_across_pages_in_chronological_order():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run the Trello normalizer")
    workflow = json.loads((WORKFLOWS_DIR / "trello-ingestion.json").read_text(encoding="utf-8"))
    code = next(n["parameters"]["jsCode"] for n in workflow["nodes"] if n["name"] == "Normalize Trello Actions")
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
    result = subprocess.run(
        [node, "-e", """
            const {code, pages} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const run = new Function('$input', '$workflow', code);
            const items = pages.map(json => ({json}));
            process.stdout.write(JSON.stringify(run({all: () => items}, {id: 'test'})));
        """],
        input=json.dumps({"code": code, "pages": pages}),
        text=True, capture_output=True, check=True, timeout=15,
    )
    events = [LayaEvent.model_validate(item["json"]) for item in json.loads(result.stdout)]

    assert [event.source.raw_event_type for event in events] == [
        "card_created", "card_commented", "card_member_added", "card_completed", "card_moved", "card_archived",
    ]
    assert {event.entity_id for event in events} == {"trello:ticket:card-id"}
    assert {event.subject.url for event in events} == {"https://trello.com/c/AbCd1234"}
    assert {event.source.connection_id for event in events} == {"test"}
    assert events[1].event_id == "evt_trello_test_1"
    assert events[1].actor.platform_handle == "boarduser"
    assert events[1].content.body == "Please review."
    assert events[2].content.metadata["participants"] == [
        {"role": "assignee", "name": "Card Member", "email": "", "handle": "cardmember"},
    ]
    assert events[4].content.body == "Card moved from To do to Done."
