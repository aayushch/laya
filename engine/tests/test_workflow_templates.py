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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for workflow Code nodes")
@pytest.mark.parametrize("team_status", [200, 403])
def test_github_team_reviews_enrich_prs_and_comments(team_status):
    workflow = json.loads((WORKFLOWS_DIR / "github-ingestion.json").read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    repo = "https://api.github.com/repos/acme/backend"
    updated = "2026-11-22T10:30:00Z"
    prs = [
        {
            "number": number,
            "base": {"repo": {"url": repo, "full_name": "acme/backend"}},
            "user": {"login": "author"},
            "requested_reviewers": [{"login": "DEV"}] if number == 3 else [],
            "requested_teams": [{"id": 21, "slug": "backend"}] if number == 4
            else [{"id": 13, "slug": "platform"}],
        }
        for number in range(1, 5)
    ]
    issues = [
        {
            "number": number,
            "repository_url": repo,
            "updated_at": updated,
            "user": {"login": "author"},
            "assignees": [{"login": "DEV"}] if number == 1 else [],
            **({"pull_request": {}} if number <= 4 else {}),
        }
        for number in (1, 3, 4, 5)
    ]
    comment = {"id": 27, "issue_url": f"{repo}/issues/2", "created_at": updated, "user": {"login": "DEV"}}
    payload = {
        "normalize": nodes["Filter & Normalize"]["parameters"]["jsCode"],
        "enrich": nodes["Add Team Review Requests"]["parameters"]["jsCode"],
        "raw": [*issues, comment, *prs],
        "previous": {
            "Get Pull Requests": prs,
            "Get GitHub Account": [{"login": "dev"}],
            "Get GitHub Teams": [
                {"statusCode": 200, "body": [{"id": 99, "slug": "backend", "organization": {"login": "other"}}]},
                {"statusCode": team_status, "body": [{"id": 13, "slug": "platform"}]},
            ],
        },
    }
    runner = """
        const { normalize, enrich, raw, previous } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        const items = data => data.map(json => ({ json }));
        const normalized = new Function('$input', '$workflow', normalize)(
          { all: () => items(raw) }, { id: 'github-workflow' },
        );
        const result = new Function('$input', '$', enrich)({ all: () => normalized }, name => ({
          all: () => items(previous[name]),
          first: () => items(previous[name])[0],
        }));
        process.stdout.write(JSON.stringify(result.map(item => item.json)));
    """
    result = subprocess.run(
        ["node", "-e", runner], input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    events = {event["subject"]["id"]: event for event in json.loads(result.stdout)}
    metadata = {key: event["content"]["metadata"] for key, event in events.items()}
    overlapping_roles = {"acme/backend/1": "assignee", "acme/backend/2": "commenter"}
    for key, role in overlapping_roles.items():
        assert events[key]["subject"]["type"] == "pull_request"
        assert metadata[key]["requested_teams"] == ["acme/platform"]
        assert {"role": role, "name": "DEV", "handle": "DEV"} in metadata[key]["participants"]
    team_reviewers = {
        key: [p for p in metadata[key]["participants"] if p["role"] == "reviewer"]
        for key in overlapping_roles
    }
    expected_reviewer = {
        "role": "reviewer",
        "name": "dev",
        "handle": "dev",
        "review_requested_via_teams": ["acme/platform"],
    }
    assert team_reviewers == {
        key: [expected_reviewer] if team_status == 200 else [] for key in overlapping_roles
    }
    assert [p for p in metadata["acme/backend/3"]["participants"] if p["role"] == "reviewer"] == [
        {"role": "reviewer", "name": "DEV", "handle": "DEV"},
    ]
    assert metadata["acme/backend/4"]["requested_teams"] == ["acme/backend"]
    assert all(p["role"] != "reviewer" for p in metadata["acme/backend/4"]["participants"])
    assert "requested_teams" not in metadata["acme/backend/5"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for workflow Code nodes")
def test_github_membership_lookup_requires_a_watched_github_repo():
    workflow = json.loads((WORKFLOWS_DIR / "github-ingestion.json").read_text(encoding="utf-8"))
    gate_name, = [edge["node"] for edge in workflow["connections"]["Get Repos"]["main"][0]]
    gate = next(node for node in workflow["nodes"] if node["name"] == gate_name)
    assert gate["type"] == "n8n-nodes-base.code", "Get Repos must check eligibility before calling GitHub"
    assert [edge["node"] for edge in workflow["connections"][gate_name]["main"][0]] == [
        "Get GitHub Account",
    ]

    runner = """
        const { code, cases } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        const gate = new Function('$input', code);
        const outputs = cases.map(repos => gate({ first: () => ({ json: { repos } }) }));
        process.stdout.write(JSON.stringify(outputs));
    """
    jira_repo = {"platform": "jira", "remote_id": "acme/project"}
    invalid_repo = {"platform": "github", "remote_id": "invalid"}
    backend = {"platform": "github", "remote_id": "acme/backend"}
    frontend = {"platform": "github", "remote_id": "acme/frontend"}
    result = subprocess.run(
        ["node", "-e", runner],
        input=json.dumps({
            "code": gate["parameters"]["jsCode"],
            "cases": [[], [jira_repo, invalid_repo], [backend, jira_repo, frontend, invalid_repo]],
        }),
        text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout) == [[], [], [{"json": {"repos": [backend, frontend]}}]]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for workflow expressions")
def test_github_pull_pagination_covers_the_polling_window():
    workflow = json.loads((WORKFLOWS_DIR / "github-ingestion.json").read_text(encoding="utf-8"))
    pulls = next(node["parameters"] for node in workflow["nodes"] if node["name"] == "Get Pull Requests")
    query = {param["name"]: param["value"] for param in pulls["queryParameters"]["parameters"]}
    assert (query["sort"], query["direction"]) == ("updated", "desc")
    pagination = pulls["options"].get("pagination", {}).get("pagination")
    assert pagination, "an active polling window can span multiple pages of pull requests"

    runner = """
        const { completeExpression } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        const complete = new Function('$response', '$json', 'return (' + completeExpression.slice(3, -2) + ')');
        const since = '2026-11-22T10:30:00.000Z';
        const page = (updated, next = true) => ({
          headers: next ? { link: '<https://api.github.com/pulls?page=2>; rel="next"' } : {},
          body: updated.map(updated_at => ({ updated_at })),
        });
        const pages = [
          page(['2026-11-22T10:31:00Z']),
          page(['2026-11-22T10:30:00Z']),
          page(['2026-11-22T10:31:00Z', '2026-11-22T10:29:59Z']),
          page(['2026-11-22T10:31:00Z'], false),
          page([]),
        ];
        process.stdout.write(JSON.stringify(pages.map(response => complete(response, { since }))));
    """
    result = subprocess.run(
        ["node", "-e", runner], input=json.dumps(pagination), text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout) == [False, False, True, True, True]
