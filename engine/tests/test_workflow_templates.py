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

import pytest

from laya.integrations.n8n_bootstrap import WORKFLOWS_DIR

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


def _normalize(platform, node_name, items, *, since=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to run an n8n code node")
    data = json.loads((WORKFLOWS_DIR / f"{platform}-ingestion.json").read_text(encoding="utf-8"))
    code = next(n["parameters"]["jsCode"] for n in data["nodes"] if n["name"] == node_name)
    result = subprocess.run(
        [node, "-e", """
            const { code, items, since } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            const run = new Function('$input', '$workflow', '$', code);
            const earlierNode = () => ({ first: () => ({ json: { since } }) });
            process.stdout.write(JSON.stringify(run({ all: () => items }, { id: 'test' }, earlierNode)));
        """],
        input=json.dumps({"code": code, "items": [{"json": item} for item in items], "since": since}),
        text=True, capture_output=True, check=True, timeout=15,
    )
    return [item["json"] for item in json.loads(result.stdout)]


def test_github_merge_has_a_stable_timestamp_and_survives_comment_filtering():
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
        "pull_request_merged", "pull_request_closed", "pull_request_merged",
        "issue_comment_created", "issue_comment_created",
    ]
    assert events[0]["timestamp"] == events[2]["timestamp"] == merged_at
    [repeat] = _normalize(
        "github", "Filter & Normalize", [dict(merged, updated_at="2026-11-22T16:02:00Z")], since=since,
    )
    assert repeat["event_id"] == events[0]["event_id"]


def test_jira_done_uses_the_latest_status_change_time_and_author():
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
    issue["fields"]["updated"] = now.isoformat()
    [repeat] = _normalize("jira", "Classify Change", [issue])
    assert repeat["event_id"] == event["event_id"]
