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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from laya.api.events import receive_event
from laya.integrations.n8n_bootstrap import WORKFLOWS_DIR
from laya.models.event import LayaEvent
from laya.pipeline.queue import process_event
from tests.conftest import insert_test_card

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


def _normalize(platform, node_name, items, *, since=None, workflow_id="test"):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run an n8n code node")
    data = json.loads((WORKFLOWS_DIR / f"{platform}-ingestion.json").read_text(encoding="utf-8"))
    code = next(n["parameters"]["jsCode"] for n in data["nodes"] if n["name"] == node_name)
    result = subprocess.run(
        [node, "-e", """
            const { code, items, since, workflowId } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const run = new Function('$input', '$workflow', '$', code);
            const earlierNode = () => ({ first: () => ({ json: { since } }) });
            process.stdout.write(JSON.stringify(run({ all: () => items }, { id: workflowId }, earlierNode)));
        """],
        input=json.dumps({
            "code": code, "items": [{"json": item} for item in items],
            "since": since, "workflowId": workflow_id,
        }),
        text=True, capture_output=True, check=True, timeout=15,
    )
    return [item["json"] for item in json.loads(result.stdout)]


async def _assert_completion_reaches_both_workflows(db, deliveries, retries):
    events = [LayaEvent.model_validate(delivery) for delivery in deliveries]
    for event in events:
        card_id = f"card-{event.source.connection_id}"
        earlier = event.model_copy(update={
            "event_id": card_id,
            "timestamp": event.timestamp - timedelta(seconds=1),
            "source": event.source.model_copy(update={"raw_event_type": "issue_updated"}),
        })
        await receive_event(earlier)
        await insert_test_card(db, card_id=card_id, event_id=card_id, status="ready", space_id="default")

    events.extend(LayaEvent.model_validate(retry) for retry in retries)
    with (
        patch("laya.pipeline.source_completion.load_settings", return_value={
            "pipeline": {"source_card_completion": True},
        }),
        patch("laya.config.load_rules", return_value={"rules": []}),
    ):
        for event in events:
            await receive_event(event)
            await process_event(event.event_id)

    cards = await db.execute_fetchall("SELECT card_id, status FROM action_cards ORDER BY card_id")
    assert [tuple(card) for card in cards] == [("card-other", "done"), ("card-test", "done")]
    completed = await db.execute_fetchall(
        "SELECT source_connection_id FROM events WHERE processing_status = 'completed' ORDER BY 1"
    )
    assert [row["source_connection_id"] for row in completed] == ["other", "test"]


@pytest.mark.asyncio
async def test_github_merge_has_a_stable_timestamp_and_survives_comment_filtering(db):
    repo = "https://api.github.com/repos/acme/backend"
    since = "2026-11-22T15:59:30Z"
    merged_at = "2026-11-22T16:00:00Z"
    merged = {
        "number": 11, "repository_url": repo, "state": "closed", "pull_request": {"merged_at": merged_at},
        "updated_at": "2026-11-22T16:01:00Z",
    }
    comment = {"id": 1, "issue_url": f"{repo}/issues/11", "created_at": merged["updated_at"]}
    unmerged = dict(merged, number=12, pull_request={"merged_at": None})
    merged_via_pull_list = dict(unmerged, number=13)
    pull_detail = {
        "number": 13, "base": {"repo": {"url": repo}}, "requested_reviewers": [], "merged_at": merged_at,
    }
    old_merge = dict(merged, number=14, pull_request={"merged_at": "2026-11-20T16:00:00Z"})
    old_merge_comment = dict(comment, id=2, issue_url=f"{repo}/issues/14")
    events = _normalize(
        "github", "Filter & Normalize",
        [merged, unmerged, merged_via_pull_list, pull_detail, comment, old_merge, old_merge_comment],
        since=since,
    )

    assert [event["source"]["raw_event_type"] for event in events] == [
        "pull_request_closed", "pull_request_closed", "pull_request_closed",
        "issue_comment_created", "issue_comment_created",
    ]
    assert events[0]["timestamp"] == events[2]["timestamp"] == merged_at
    [other_merge, other_unmerged] = _normalize(
        "github", "Filter & Normalize", [merged, unmerged], since=since, workflow_id="other",
    )
    assert other_unmerged["event_id"] == events[1]["event_id"]
    updated = dict(merged, updated_at="2026-11-22T16:02:00Z")
    retries = [
        _normalize("github", "Filter & Normalize", [updated], since=since, workflow_id=workflow_id)[0]
        for workflow_id in ("test", "other")
    ]
    await _assert_completion_reaches_both_workflows(db, [events[0], other_merge], retries)


@pytest.mark.asyncio
async def test_jira_done_uses_the_latest_status_change_time_and_author(db):
    now = datetime.now(timezone.utc)
    moved = (now - timedelta(seconds=20)).isoformat()
    later_edit = (now - timedelta(seconds=10)).isoformat()
    issue = {
        "id": "42", "key": "DEMO-42",
        "fields": {
            "summary": "Fix", "status": {"name": "Done"}, "updated": later_edit,
            "created": (now - timedelta(days=1)).isoformat(),
        },
        "changelog": {"histories": [
            {"created": later_edit, "author": {"displayName": "Editor"},
             "items": [{"field": "priority", "fromString": "Low", "toString": "High"}]},
            {"created": moved, "author": {"displayName": "Mover"},
             "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
            {"created": (now - timedelta(seconds=30)).isoformat(),
             "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
        ]},
    }
    [event] = _normalize("jira", "Classify Change", [issue])
    assert event["source"]["raw_event_type"] == "issue_resolved"
    assert event["content"]["metadata"]["jira_new_status"] == "Done"
    assert event["timestamp"] == moved
    assert event["actor"]["name"] == "Mover"
    [other_event] = _normalize("jira", "Classify Change", [issue], workflow_id="other")
    issue["fields"]["updated"] = now.isoformat()
    retries = [
        _normalize("jira", "Classify Change", [issue], workflow_id=workflow_id)[0]
        for workflow_id in ("test", "other")
    ]
    await _assert_completion_reaches_both_workflows(db, [event, other_event], retries)


def test_jira_changelog_pagination_keeps_recent_changes_with_their_issues():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run an n8n code node")
    workflow = json.loads((WORKFLOWS_DIR / "jira-ingestion.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=1)).isoformat()
    moved = (now - timedelta(seconds=40)).isoformat()
    edited = (now - timedelta(seconds=20)).isoformat()
    old_entry = {"created": old, "items": [{"field": "summary", "toString": "Previous title"}]}
    done = {
        "created": moved, "author": {"displayName": "Mover"},
        "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
    }
    priority = {"created": edited, "items": [{"field": "priority", "fromString": "Low", "toString": "High"}]}
    issues = [
        {
            "id": str(index), "key": f"DEMO-{index}", "self": f"https://jira.example/rest/api/3/issue/{index}",
            "fields": {"summary": "Fix", "status": {"name": "Done"}, "created": old, "updated": edited},
        }
        for index in range(3)
    ]
    histories = [
        [old_entry] * 199 + [done] + [{"created": edited, "items": []}],
        [old_entry] * 501 + [priority],
        [],
    ]
    result = subprocess.run(
        [node, "-e", """
            const { workflow, issues, histories } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const params = name => workflow.nodes.find(node => node.name === name).parameters;
            const evaluate = (expr, locals) => new Function(
                ...Object.keys(locals), 'return (' + expr.slice(3, -2) + ')'
            )(...Object.values(locals));
            const get = params('Get Changelogs');
            const pagination = get.options.pagination?.pagination;
            const offsetParam = pagination?.parameters?.parameters.find(
                param => param.type === 'qs' && param.name === 'startAt'
            );
            const offsets = issues.map(() => []);
            const pages = issues.flatMap((issue, issueIndex) => {
                const url = new URL(evaluate(get.url, { $json: issue }));
                const maxResults = Number(url.searchParams.get('maxResults'));
                const history = histories[issueIndex];
                const output = [];
                let $response = {};
                for (let $pageCount = 0; $pageCount <= history.length; $pageCount++) {
                    const startAt = offsetParam ? evaluate(offsetParam.value, { $response, $pageCount }) : 0;
                    offsets[issueIndex].push(startAt);
                    const body = {
                        startAt, maxResults, total: history.length, isLast: startAt + maxResults >= history.length,
                        values: history.slice(startAt, startAt + maxResults),
                    };
                    output.push({ json: body, pairedItem: { item: issueIndex } });
                    $response = { body, statusCode: 200 };
                    if (!offsetParam || evaluate(pagination.completeExpression, { $response, $pageCount: $pageCount + 1 })) {
                        return output;
                    }
                }
                throw new Error('Changelog pagination did not stop');
            });
            const run = (name, items) => new Function('$input', '$workflow', params(name).jsCode)(
                { all: () => items }, { id: 'test' }
            );
            const wrapped = run('Wrap Changelog', pages);
            const merged = issues.map((issue, index) => ({ json: { ...issue, ...wrapped[index]?.json } }));
            const events = run('Classify Change', merged).map(item => item.json);
            process.stdout.write(JSON.stringify({ events, offsets }));
        """],
        input=json.dumps({"workflow": workflow, "issues": issues, "histories": histories}),
        text=True, capture_output=True, check=True, timeout=15,
    )
    output = json.loads(result.stdout)
    assert [(event["subject"]["id"], event["source"]["raw_event_type"]) for event in output["events"]] == [
        ("DEMO-0", "issue_resolved"), ("DEMO-1", "issue_priority_changed"), ("DEMO-2", "issue_updated"),
    ]
    assert output["events"][0]["timestamp"] == moved
    assert output["events"][0]["actor"]["name"] == "Mover"
    assert output["offsets"] == [[0, 200, 100], [0, 500], [0]]


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_enabled", [False, True])
async def test_github_merges_still_obey_closed_pr_drop_rules(db, completion_enabled):
    merged_at = "2026-11-22T16:00:00Z"
    [delivery] = _normalize("github", "Filter & Normalize", [{
        "number": 11,
        "repository_url": "https://api.github.com/repos/acme/backend",
        "state": "closed",
        "pull_request": {"merged_at": merged_at},
        "updated_at": merged_at,
    }], since="2026-11-22T15:59:30Z")
    event = LayaEvent.model_validate(delivery)
    await receive_event(event)
    rules = {"rules": [{
        "name": "Drop closed PRs",
        "condition": {
            "field": "source.raw_event_type", "operator": "equals", "value": "pull_request_closed",
        },
    }]}

    with (
        patch("laya.pipeline.source_completion.load_settings", return_value={
            "pipeline": {"source_card_completion": completion_enabled},
        }),
        patch("laya.pipeline.rules.load_rules", return_value=rules),
        patch("laya.pipeline.router.run_router", new_callable=AsyncMock, return_value=None) as route,
    ):
        await process_event(event.event_id)

    [row] = await db.execute_fetchall("SELECT processing_status, filter_rule FROM events")
    assert tuple(row) == ("filtered", "Drop closed PRs")
    route.assert_not_awaited()
