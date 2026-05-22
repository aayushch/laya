# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Computed placeholder helpers for preview summary templates.

``EgressCapability.summary_template`` strings are rendered against the
enriched egress payload.  Some summaries need multi-field references that
are cleaner to compute than to express as raw templates — e.g. GitHub's
``owner/repo#123`` combines three payload fields with specific glue.  This
module centralises those helpers.

The resolved values are injected into the format-map under synthetic keys
(``{gh_ref}``, ``{bb_ref}``) before ``.format_map`` runs.
"""

from __future__ import annotations


def format_github_ref(payload: dict) -> str:
    """Render a GitHub issue/PR reference like ``owner/repo#123``.

    Falls back to ``"#<num>"`` when owner/repo are missing, or
    ``"unknown"`` when no number is present.
    """
    owner = payload.get("owner") or ""
    repo = payload.get("repo") or ""
    num = payload.get("issue_number") or payload.get("pr_number") or ""
    if owner and repo and num:
        return f"{owner}/{repo}#{num}"
    if num:
        return f"#{num}"
    return "unknown"


def format_bitbucket_ref(payload: dict) -> str:
    """Render a Bitbucket PR reference like ``workspace/repo PR #42``."""
    ws = payload.get("workspace") or ""
    repo = payload.get("repo") or ""
    pr = payload.get("pr_id") or ""
    if ws and repo and pr:
        return f"{ws}/{repo} PR #{pr}"
    if pr:
        return f"PR #{pr}"
    return "unknown"


def computed_placeholders(payload: dict) -> dict:
    """Return synthetic placeholders to inject into a summary format-map.

    Keys here are additive to the payload itself — callers merge them into
    the same dict passed to ``str.format_map``.
    """
    return {
        "gh_ref": format_github_ref(payload),
        "bb_ref": format_bitbucket_ref(payload),
    }
