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
