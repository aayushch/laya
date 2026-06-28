# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Parity test: every raw_event_type declared terminal in
registry._TERMINAL_EVENT_TYPES must actually be emittable by that platform's
n8n/workflows/<platform>-ingestion.json.

This prevents the drift that previously left the terminal set with five dead
entries (pr_closed, ticket_closed, ticket_resolved, build_succeeded,
deploy_completed) that no workflow emits, and missing github's real
`pull_request_closed`.

Inbound raw_event_types are produced by JS ternaries / a `rawEventType`
variable inside the ingestion Code nodes (not a clean Switch like the egress
executors), so we can't parse a single routing node. Instead we assert the
weaker-but-sufficient ⊆ direction: each declared terminal type must appear as a
quoted string literal somewhere in the ingestion workflow. The reverse
direction ("every terminal-ish emitted type is declared") is a semantic
judgment a test can't make, so it is intentionally not enforced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from laya.egress.registry import _TERMINAL_EVENT_TYPES

# engine/tests/test_X.py -> engine/tests -> engine -> repo -> n8n/workflows
_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "n8n" / "workflows"

# registry platform key -> ingestion workflow filename
_PLATFORM_TO_WORKFLOW = {
    "github": "github-ingestion.json",
    "bitbucket": "bitbucket-ingestion.json",
    "jira": "jira-ingestion.json",
    "linear": "linear-ingestion.json",
}


def _flatten_terminal_pairs():
    for platform, event_types in _TERMINAL_EVENT_TYPES.items():
        for et in sorted(event_types):
            yield platform, et


@pytest.mark.parametrize("platform,event_type", list(_flatten_terminal_pairs()))
def test_declared_terminal_event_is_emittable(platform: str, event_type: str):
    workflow_file = _PLATFORM_TO_WORKFLOW.get(platform)
    assert workflow_file, (
        f"_TERMINAL_EVENT_TYPES declares platform {platform!r} but this test has "
        f"no ingestion workflow mapping for it. Add one to _PLATFORM_TO_WORKFLOW."
    )
    workflow_path = _WORKFLOWS_DIR / workflow_file
    assert workflow_path.exists(), f"Missing workflow: {workflow_path}"

    text = workflow_path.read_text()
    # Match the event-type as a single- or double-quoted JS/JSON string literal.
    pattern = re.compile(r"""['"]%s['"]""" % re.escape(event_type))
    assert pattern.search(text), (
        f"registry._TERMINAL_EVENT_TYPES[{platform!r}] declares {event_type!r} as "
        f"terminal, but {workflow_file} never emits that raw_event_type. Either fix "
        f"the registry or the workflow."
    )


def test_every_terminal_platform_has_workflow_mapping():
    """Guards against declaring terminal events for a platform this test can't
    verify (e.g. a future platform added to the registry without a mapping)."""
    unmapped = set(_TERMINAL_EVENT_TYPES) - set(_PLATFORM_TO_WORKFLOW)
    assert not unmapped, (
        f"_TERMINAL_EVENT_TYPES has platform(s) with no ingestion-workflow mapping "
        f"in this test: {sorted(unmapped)}. Add them to _PLATFORM_TO_WORKFLOW."
    )
