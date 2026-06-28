# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Bitbucket-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

import re

from laya.config import load_repos
from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform
from laya.egress.platforms.github import GITHUB_DRAFT_SCHEMA

_EVENT_ID_PR_RE = re.compile(r"^evt_bb_pr_.+_(?P<id>\d+)_\d+$")


class BitbucketPlatform(Platform):
    name = "bitbucket"
    terminal_event_types = frozenset({"pr_merged", "pr_declined"})
    platform_hint = "a Bitbucket PR or comment"
    chapter_default = "Code"
    draft_schema = GITHUB_DRAFT_SCHEMA  # shares github's draft schema
    source_ref_config = {"pr_format": "PR #{id}", "default_format": "#{id}"}
    compose_guidance = (
        "You are composing a BITBUCKET PR or COMMENT. Field requirements:\n"
        "- 'repo': repository in workspace/repo format.\n"
        "- 'title': concise one-line PR title.\n"
        "- 'body': full body text (supports markdown)."
    )
    polish_guidance = (
        "Bitbucket comment — technical tone, direct; preserve code blocks, @mentions, and PR references."
    )

    capabilities = [
        EgressCapability(
            action_type="comment_pr",
            label="Comment on PR",
            requires_fields=["workspace", "repo", "pr_id", "comment"],
            content_fields=["comment"],
            description="Post a comment on a Bitbucket pull request.",
            summary_template="Comment on {bb_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Approve a Bitbucket pull request.",
            summary_template="Approve {bb_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="decline_pr",
            label="Decline PR",
            requires_fields=["workspace", "repo", "pr_id"],
            description="Decline a Bitbucket pull request.",
            summary_template="Decline {bb_ref}",
            warnings=["This will decline the pull request."],
            impact="high",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["workspace", "repo", "pr_id"],
            optional_fields=["merge_strategy", "close_source_branch"],
            optional_content_fields=["merge_strategy", "close_source_branch"],
            description="Merge a Bitbucket pull request.",
            summary_template="Merge {bb_ref}",
            warnings=["This will merge the pull request. This action cannot be undone."],
            impact="high",
        ),
    ]

    def _is_cloud_host(self, host: str) -> bool:
        """True for Bitbucket Cloud repos.

        A repo is Cloud when its configured host is empty (legacy repos predate the
        ``host`` field and were Cloud-only) or is exactly ``bitbucket.org`` (or a
        subdomain of it). Any other host is a self-hosted Bitbucket Server / Data
        Center instance.

        ``host`` is a bare hostname (scheme and port are stripped when the remote URL
        is parsed — see ``parse_remote_url`` in the Tauri shell). We anchor the match
        with equality + a dotted-suffix check rather than a substring ``in`` test so a
        look-alike host such as ``bitbucket.org.evil.com`` or ``notbitbucket.org`` is
        correctly treated as self-hosted instead of Cloud.
        """
        h = host.strip().lower()
        return h == "" or h == "bitbucket.org" or h.endswith(".bitbucket.org")

    def ensure_cloud_repo(self, payload: dict) -> None:
        """Raise ``ValueError`` if the action targets a self-hosted Bitbucket repo.

        Outbound actions are dispatched through the n8n executor against the Bitbucket
        *Cloud* REST API (``api.bitbucket.org``), which an on-prem Server / Data Center
        host won't answer. We match the enriched payload's ``workspace``/``repo`` against
        ``repos.json`` and reject when the configured repo carries a non-Cloud host, so
        the caller fails fast with a clear message instead of firing a doomed Cloud call.
        """
        workspace = payload.get("workspace") or ""
        repo = payload.get("repo") or ""
        if not workspace or not repo:
            return  # no identifiers to match; normal validation handles missing fields
        remote_id = f"{workspace}/{repo}"
        for r in load_repos().get("repos", []):
            if r.get("platform") != "bitbucket" or r.get("remote_id") != remote_id:
                continue
            if not self._is_cloud_host(r.get("host", "")):
                raise ValueError(
                    f"On-prem Bitbucket Server is not supported for outbound actions "
                    f"(repo '{remote_id}' is hosted on '{r.get('host')}'). "
                    f"Only Bitbucket Cloud (bitbucket.org) actions can be executed."
                )
            return

    def identifiers_from_event(
        self,
        action_type: str,
        event_id: str | None,
        content_metadata: dict,
        event_row: dict,
        self_emails: set[str] | None = None,
    ) -> dict:
        """Derive Bitbucket identifiers from the event.

        Prefer ``content_metadata['bb_repository']`` ("workspace/repo") for
        workspace+repo to avoid event_id regex ambiguity.  The PR id is
        unambiguous in the event_id suffix.
        """
        ids: dict = {}

        repo_full = (content_metadata or {}).get("bb_repository") or ""
        if "/" in repo_full:
            workspace, repo = repo_full.split("/", 1)
            ids["workspace"] = workspace
            ids["repo"] = repo

        if event_id:
            m = _EVENT_ID_PR_RE.match(event_id)
            if m:
                ids["pr_id"] = m.group("id")

        comment_id = (content_metadata or {}).get("bb_comment_id")
        if comment_id:
            ids["comment_id"] = str(comment_id)

        return ids

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Normalize Bitbucket executor payload fields."""
        p = dict(payload)

        # Split "workspace/repo" into separate workspace and repo fields
        repo_val = p.get("repo", "")
        if isinstance(repo_val, str) and "/" in repo_val and "workspace" not in p:
            workspace, repo = repo_val.split("/", 1)
            p["workspace"] = workspace
            p["repo"] = repo

        # Normalize comment field
        if action_type == "comment_pr" and "comment" not in p:
            p["comment"] = (
                p.pop("body", None)
                or p.pop("message", None)
                or p.pop("content", None)
                or p.pop("text", None)
                or p.pop("raw", None)
                or ""
            )

        # Normalize comment_id for thread replies (may come as int or string)
        if "comment_id" in p and p["comment_id"]:
            p["comment_id"] = str(p["comment_id"])

        # Normalize PR ID
        if "pr_id" not in p:
            p["pr_id"] = p.pop("pr_number", None) or p.pop("number", None) or ""

        # Coerce pr_id to string
        if p.get("pr_id") is not None:
            p["pr_id"] = str(p["pr_id"])

        # Merge strategy default
        if action_type == "merge_pr" and "merge_strategy" not in p:
            p["merge_strategy"] = "squash"

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors."""
        errors = []

        if not payload.get("workspace"):
            errors.append("Missing 'workspace' (Bitbucket workspace slug)")
        if not payload.get("repo"):
            errors.append("Missing 'repo' (Bitbucket repository slug)")

        if action_type in ("comment_pr", "approve_pr", "decline_pr", "merge_pr"):
            if not payload.get("pr_id"):
                errors.append("Missing 'pr_id' (pull request ID)")

        if action_type == "comment_pr":
            if not payload.get("comment"):
                errors.append("Missing 'comment' body")

        if action_type == "create_pr":
            if not payload.get("title"):
                errors.append("Missing PR 'title'")
            if not payload.get("source_branch"):
                errors.append("Missing 'source_branch'")
            if not payload.get("dest_branch"):
                errors.append("Missing 'dest_branch'")

        return errors


PLATFORM = BitbucketPlatform()
