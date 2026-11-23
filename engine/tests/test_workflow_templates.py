# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Static guards over the bundled n8n workflow templates.

Every node that calls the engine must send the n8n token, or its clone is
rejected once enforcement is on; the event POST nodes retry so a brief engine
restart does not lose a poll.
"""

import json
import re

import pytest

from laya.integrations.n8n_bootstrap import WORKFLOWS_DIR

TOKEN_HEADER = "X-Laya-N8n-Token"
TOKEN_VALUE = "={{ $env.LAYA_N8N_TOKEN }}"
_REPO_LISTING = re.compile(r"LAYA_ENGINE_URL \}\}/repos(?:\?|$|[^/\w-])")


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


def _repo_listing_violations(templates: list[tuple[str, dict]]) -> tuple[set, list]:
    """(names of templates that list repos, repo-listing nodes without workflow_id).

    A node lists repos when its URL or any of its parameters calls the engine's
    ``/repos`` route, with or without a query string; ``/repos/...`` and
    ``/spaces/{id}/repos`` are other routes.
    """
    seen: set[str] = set()
    missing: list[str] = []
    for name, data in templates:
        for node in _engine_nodes(data):
            url = node["parameters"].get("url", "")
            if not any(
                _REPO_LISTING.search(s) for s in (url, json.dumps(node["parameters"]))
            ):
                continue
            seen.add(name)
            if "workflow_id={{ encodeURIComponent($workflow.id) }}" not in url:
                missing.append(f"{name}: {node['name']}")
    return seen, missing


@pytest.mark.skipif(not WORKFLOWS_DIR.exists(), reason="bundled workflows not present")
def test_repo_listing_nodes_pass_their_workflow_id():
    """GET /repos scopes the list to the caller's space only when the clone
    sends its own n8n workflow id."""
    seen, missing = _repo_listing_violations(_templates())
    assert sorted(seen) == [
        "bitbucket-ingestion.json", "bitbucket-server-ingestion.json", "github-ingestion.json",
    ]
    assert not missing, f"repo-listing nodes without workflow_id: {missing}"


def _http_node(name: str, **parameters) -> dict:
    return {"name": name, "type": "n8n-nodes-base.httpRequest", "parameters": parameters}


def test_repo_listing_guard_selects_every_repo_listing_node():
    """The guard selects every node that calls GET /repos, whether or not its
    URL carries a query string, and reports each one without workflow_id."""
    correct = _http_node(
        "Fetch Repos",
        url="={{ $env.LAYA_ENGINE_URL }}/repos?platform=github"
            "&workflow_id={{ encodeURIComponent($workflow.id) }}",
    )
    query_less = _http_node("Fetch Repos Again", url="={{ $env.LAYA_ENGINE_URL }}/repos")
    send_query = _http_node(
        "List Repos",
        url="={{ $env.LAYA_ENGINE_URL }}/repos",
        sendQuery=True,
        queryParameters={"parameters": [
            {"name": "platform", "value": "github"},
            {"name": "workflow_id", "value": "={{ $workflow.id }}"},
        ]},
    )
    space_repos = _http_node("Space Repos", url="={{ $env.LAYA_ENGINE_URL }}/spaces/x/repos")

    seen, missing = _repo_listing_violations([
        ("a.json", {"nodes": [correct, query_less]}),
        ("b.json", {"nodes": [send_query]}),
        ("c.json", {"nodes": [space_repos]}),
    ])

    assert seen == {"a.json", "b.json"}
    assert missing == ["a.json: Fetch Repos Again", "b.json: List Repos"]
