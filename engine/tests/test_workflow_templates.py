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

@pytest.fixture
def github_code():
    node = shutil.which("node")
    workflow = WORKFLOWS_DIR / "github-ingestion.json"
    if not node or not workflow.exists():
        pytest.skip("GitHub workflow and Node.js required")
    nodes = {n["name"]: n for n in json.loads(workflow.read_text(encoding="utf-8"))["nodes"]}

    def run(name, items, workflow_id="github_test", since="2026-11-22T10:00:00Z"):
        runner = """
const {code, items, workflowId, since} = JSON.parse(require('node:fs').readFileSync(0));
const input = {all: () => items.map(json => ({json}))};
const selector = () => ({first: () => ({json: {since}})});
const output = new Function('$input', '$workflow', '$', code)(input, {id: workflowId}, selector);
process.stdout.write(JSON.stringify(output.map(item => item.json)));
"""
        result = subprocess.run(
            [node, "-e", runner],
            input=json.dumps({
                "code": nodes[name]["parameters"]["jsCode"],
                "items": items,
                "workflowId": workflow_id,
                "since": since,
            }),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return json.loads(result.stdout)

    return run


REPO_URL = "https://api.github.com/repos/acme/backend"
PR_TIME = "2026-11-22T10:01:00Z"
OLD_TIME = "2026-11-21T10:00:00Z"
MERGED_AT = "2026-11-22T10:00:30Z"


def _pull(number=42, updated_at=PR_TIME):
    return {
        "url": f"{REPO_URL}/pulls/{number}",
        "number": number,
        "title": "Handle empty input",
        "updated_at": updated_at,
        "base": {"repo": {"url": REPO_URL}},
        "user": {"login": "pr-author"},
        "assignees": [{"login": "assignee"}],
        "requested_reviewers": [{"login": "requested-reviewer"}],
    }


def _inline(number=42):
    return {
        "id": 100,
        "pull_request_review_id": 80,
        "pull_request_url": f"{REPO_URL}/pulls/{number}",
        "html_url": f"https://github.com/acme/backend/pull/{number}#discussion_r100",
        "created_at": PR_TIME,
        "updated_at": PR_TIME,
        "user": {"login": "reviewer"},
        "body": "@pr-author Please add a guard.",
        "path": "app.py",
        "line": 12,
        "diff_hunk": "@@ -10,3 +10,4 @@",
        "in_reply_to_id": 99,
    }


def test_github_review_requests_cover_recent_pulls_and_missing_inline_parents(github_code):
    requests = github_code("Build PR Review Requests", [
        _pull(), _pull(43, OLD_TIME), _inline(), _inline(44), _inline(44),
    ])
    assert requests == [
        {"owner": "acme", "repo": "backend", "number": "42", "suffix": "/reviews"},
        {"owner": "acme", "repo": "backend", "number": "44", "suffix": ""},
    ]


@pytest.mark.parametrize(
    ("merged_at", "closure_type"),
    [(None, "pull_request_closed"), (MERGED_AT, "pull_request_closed")],
)
def test_github_normalizes_review_activity_and_preserves_pr_closure(github_code, merged_at, closure_type):
    review = {
        "id": 100,
        "pull_request_url": f"{REPO_URL}/pulls/42",
        "html_url": "https://github.com/acme/backend/pull/42#pullrequestreview-100",
        "submitted_at": PR_TIME,
        "state": "APPROVED",
        "user": {"login": "reviewer"},
    }
    issue = {
        **_pull(),
        "repository_url": REPO_URL,
        "pull_request": {"url": f"{REPO_URL}/pulls/42", "merged_at": merged_at},
        "state": "closed",
        "created_at": OLD_TIME,
        "closed_at": merged_at or PR_TIME,
    }
    del issue["base"], issue["requested_reviewers"]
    ordinary = {
        "id": 100, "issue_url": f"{REPO_URL}/issues/42", "created_at": PR_TIME,
        "user": {"login": "teammate"}, "body": "Thanks for the update.",
    }
    inputs = [
        _pull(), issue, ordinary, _inline(), review,
        {**review, "id": 101, "state": "CHANGES_REQUESTED", "body": "Please add tests."},
        {**review, "id": 102, "submitted_at": OLD_TIME},
        {**review, "id": 103, "state": "PENDING", "submitted_at": None},
        {**review, "id": 104, "state": "COMMENTED"},
        {**review, "id": 105, "state": "DISMISSED"},
    ]
    output = github_code("Filter & Normalize", inputs)
    events = [LayaEvent.model_validate(item) for item in output]
    closed, conversation, inline, approved, changes = events
    assert closed.source.raw_event_type == closure_type
    assert closed.content.metadata.get("merged_at") == merged_at
    if merged_at:
        assert closed.timestamp.isoformat() == "2026-11-22T10:00:30+00:00"
        assert closed.event_id != github_code(
            "Filter & Normalize", [_pull(), issue], workflow_id="github_other",
        )[0]["event_id"]
    assert conversation.source.raw_event_type == inline.source.raw_event_type == "issue_comment_created"
    assert "comment_kind" not in conversation.content.metadata
    assert inline.content.metadata["comment_kind"] == "pull_request_review"
    assert inline.content.metadata["path"] == "app.py"
    assert inline.content.metadata["line"] == 12
    assert inline.content.metadata["in_reply_to_id"] == 99
    assert inline.subject.url.endswith("#discussion_r100")
    assert approved.source.raw_event_type == "pull_request_review_approved"
    assert approved.subject.url.endswith("#pullrequestreview-100")
    assert approved.content.body == ""
    assert changes.source.raw_event_type == "pull_request_review_changes_requested"
    assert changes.content.body == "Please add tests."

    for event in (inline, approved, changes):
        assert event.actor.platform_handle == "reviewer"
        assert event.subject.id == closed.subject.id == "acme/backend/42"
        assert event.subject.type == "pull_request"
        assert event.subject.title == "Handle empty input"
        assert event.content.metadata["item_participants_complete"]
        assert {p["handle"] for p in event.content.metadata["participants"] if p["role"] != "commenter"} == {
            "pr-author", "assignee", "requested-reviewer",
        }
    assert len({event.event_id for event in events}) == len(events)
    replay = github_code("Filter & Normalize", [
        {**_inline(), "updated_at": "2026-11-22T10:02:00Z", "body": "An edited comment."}, review,
    ])
    assert [item["event_id"] for item in replay] == [inline.event_id, approved.event_id]
    other_connection = github_code("Filter & Normalize", [review], workflow_id="github_other")
    assert other_connection[0]["event_id"] != approved.event_id


def test_github_inline_comment_uses_fallback_pr_context(github_code):
    comment = _inline(44)
    event, = github_code("Filter & Normalize", [comment])
    assert event["subject"]["type"] == "pull_request"
    assert event["subject"]["title"] == "PR #44"
    assert not event["content"]["metadata"]["item_participants_complete"]

    enriched, = github_code("Filter & Normalize", [comment, _pull(44, OLD_TIME)])
    assert enriched["event_id"] == event["event_id"]
    assert enriched["subject"]["title"] == "Handle empty input"
    assert enriched["content"]["metadata"]["item_participants_complete"]


def test_github_inline_comment_does_not_reemit_an_earlier_merge(github_code):
    issue = {
        "number": 42,
        "repository_url": REPO_URL,
        "pull_request": {"merged_at": OLD_TIME},
        "state": "closed",
        "created_at": OLD_TIME,
        "closed_at": OLD_TIME,
        "updated_at": PR_TIME,
    }
    event, = github_code("Filter & Normalize", [issue, _inline()])
    assert event["source"]["raw_event_type"] == "issue_comment_created"
