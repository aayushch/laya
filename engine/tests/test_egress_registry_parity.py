"""Parity test: registry.get_capabilities(platform) must match the action_types
handled by n8n/workflows/<platform>-executor.json's Switch node.

This test prevents drift between the capability registry (what Laya advertises)
and the n8n executor workflows (what Laya can actually execute). If a
contributor adds a new action to an executor workflow, this test fails until
the registry is updated to match — and vice versa.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laya.egress.registry import get_capabilities

# Path to the n8n workflow directory, resolved relative to the repo root.
# engine/tests/test_X.py -> engine/tests -> engine -> repo -> n8n/workflows
_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "n8n" / "workflows"

# Mapping from registry platform key -> executor workflow filename.
# Platforms without an n8n executor (e.g. smtp) are deliberately omitted and
# are validated separately by the smtp backend.
_PLATFORM_TO_WORKFLOW = {
    "gmail": "gmail-executor.json",
    "outlook": "outlook-email-executor.json",
    "jira": "jira-executor.json",
    "linear": "linear-executor.json",
    "notion": "notion-executor.json",
    "github": "github-executor.json",
    "bitbucket": "bitbucket-executor.json",
    "slack": "slack-executor.json",
    "calendar": "google-calendar-executor.json",
    "outlook_calendar": "outlook-calendar-executor.json",
}

# Calendar executors have no Switch node — they handle a single implicit
# action (create_event). This set tells the parser to expect that shape.
_SINGLE_ACTION_WORKFLOWS = {
    "calendar": "create_event",
    "outlook_calendar": "create_event",
}


def _extract_switch_action_types(workflow: dict) -> set[str]:
    """Walk a workflow's nodes, find the Switch that routes on action_type,
    and collect every rightValue string it branches on."""
    actions: set[str] = set()
    for node in workflow.get("nodes", []):
        if node.get("type") != "n8n-nodes-base.switch":
            continue
        rules = node.get("parameters", {}).get("rules", {}).get("values", [])
        for rule in rules:
            for cond in rule.get("conditions", {}).get("conditions", []):
                left = cond.get("leftValue", "")
                if "action_type" not in left:
                    continue
                right = cond.get("rightValue")
                if isinstance(right, str) and right:
                    actions.add(right)
    return actions


@pytest.mark.parametrize("platform,workflow_file", _PLATFORM_TO_WORKFLOW.items())
def test_registry_matches_executor(platform: str, workflow_file: str):
    workflow_path = _WORKFLOWS_DIR / workflow_file
    assert workflow_path.exists(), f"Missing workflow: {workflow_path}"

    workflow = json.loads(workflow_path.read_text())

    if platform in _SINGLE_ACTION_WORKFLOWS:
        # Single-action workflows don't have a Switch; assert the registry
        # entry contains exactly the expected implicit action.
        expected = {_SINGLE_ACTION_WORKFLOWS[platform]}
    else:
        expected = _extract_switch_action_types(workflow)
        assert expected, f"No action_types found in Switch node of {workflow_file}"

    registry_actions = {c.action_type for c in get_capabilities(platform)}

    missing_in_registry = expected - registry_actions
    missing_in_executor = registry_actions - expected

    assert not missing_in_registry, (
        f"{workflow_file} handles action(s) not declared in registry[{platform!r}]: "
        f"{sorted(missing_in_registry)}. "
        f"Add an EgressCapability entry to engine/laya/egress/registry.py."
    )
    assert not missing_in_executor, (
        f"registry[{platform!r}] advertises action(s) the executor does not "
        f"support: {sorted(missing_in_executor)}. "
        f"Either remove from registry or add routing in {workflow_file}."
    )


def test_all_executor_workflows_covered():
    """Every *-executor.json in the workflows directory (except smtp-less
    shapes) must have a registry counterpart. Catches the case where a new
    executor file is added without updating the registry."""
    executor_files = {p.name for p in _WORKFLOWS_DIR.glob("*-executor.json")}
    covered = set(_PLATFORM_TO_WORKFLOW.values())
    uncovered = executor_files - covered
    assert not uncovered, (
        f"Executor workflow(s) with no registry entry: {sorted(uncovered)}. "
        f"Add a mapping to _PLATFORM_TO_WORKFLOW in this test and an "
        f"entry in engine/laya/egress/registry.py."
    )
