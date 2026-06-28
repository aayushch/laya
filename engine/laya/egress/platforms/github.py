# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""GitHub-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

import re

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform

_ISSUE_KEY_RE = re.compile(
    r"(?:https?://github\.com/)?([^/\s]+)/([^/#\s]+?)(?:/(?:issues|pull))?[/#](\d+)"
)
_EVENT_ID_RE = re.compile(
    r"^evt_github_(?P<kind>issue|pr|comment)_(?P<rest>.+)_(?P<num>\d+)_\d+$"
)

# Owned here so bitbucket.py can reference the same object (preserving the
# identity-alias the registry used: bitbucket shared github's draft schema).
GITHUB_DRAFT_SCHEMA: dict = {
    "name": "github_draft",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": (
                    "Repository in owner/repo format (e.g. 'acme/backend'). "
                    "Empty string if unknown."
                ),
            },
            "title": {
                "type": "string",
                "description": "Issue or PR title — a concise one-line description. Empty string if not applicable.",
            },
            "body": {
                "type": "string",
                "description": "Issue/PR/comment body text (supports markdown).",
            },
        },
        "required": ["repo", "title", "body"],
        "additionalProperties": False,
    },
}


class GithubPlatform(Platform):
    name = "github"
    terminal_event_types = frozenset({"pull_request_closed", "issue_closed"})
    platform_hint = "a GitHub issue, PR, or comment"
    chapter_default = "Code"
    draft_schema = GITHUB_DRAFT_SCHEMA
    source_ref_config = {"pr_format": "PR #{id}", "default_format": "#{id}"}
    compose_guidance = (
        "You are composing a GITHUB ISSUE, PR, or COMMENT. Field requirements:\n"
        "- 'repo': repository in owner/repo format (e.g. 'acme/backend').\n"
        "- 'title': concise one-line issue/PR title.\n"
        "- 'body': full body text (supports markdown)."
    )
    polish_guidance = (
        "GitHub comment — technical tone, direct; preserve code blocks, "
        "@mentions, and issue/PR references."
    )

    capabilities = [
        EgressCapability(
            action_type="comment",
            label="Comment on Issue",
            requires_fields=["owner", "repo", "issue_number", "comment"],
            content_fields=["comment"],
            description="Post a comment on a GitHub issue or PR.",
            summary_template="Comment on {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="close_issue",
            label="Close Issue",
            requires_fields=["owner", "repo", "issue_number"],
            optional_fields=["comment"],
            optional_content_fields=["comment"],
            description="Close a GitHub issue.",
            summary_template="Close {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="create_issue",
            label="Create Issue",
            requires_fields=["owner", "repo", "title"],
            optional_fields=["body", "labels", "assignees"],
            content_fields=["title"],
            optional_content_fields=["body", "labels", "assignees"],
            description="Create a new GitHub issue.",
            summary_template="Create issue in {owner}/{repo}: '{title}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="approve_pr",
            label="Approve PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["comment"],
            optional_content_fields=["comment"],
            description="Approve a pull request.",
            summary_template="Approve PR {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="request_changes",
            label="Request Changes",
            requires_fields=["owner", "repo", "pr_number", "comment"],
            content_fields=["comment"],
            description="Request changes on a pull request.",
            summary_template="Request changes on PR {gh_ref}",
            impact="medium",
        ),
        EgressCapability(
            action_type="merge_pr",
            label="Merge PR",
            requires_fields=["owner", "repo", "pr_number"],
            optional_fields=["merge_method", "commit_title"],
            optional_content_fields=["merge_method", "commit_title"],
            description="Merge a pull request.",
            summary_template="Merge PR {gh_ref}",
            warnings=["This will merge the pull request. This action cannot be undone."],
            impact="high",
        ),
    ]

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Normalize GitHub executor payload fields."""
        p = dict(payload)

        # Split "owner/repo" into separate owner and repo fields
        repo_val = p.get("repo", "")
        if isinstance(repo_val, str) and "/" in repo_val and "owner" not in p:
            owner, repo = repo_val.split("/", 1)
            p["owner"] = owner
            p["repo"] = repo

        # Normalize comment field for comment actions
        if action_type in ("comment", "close_issue") and "comment" not in p:
            p["comment"] = (
                p.pop("body", None)
                or p.pop("message", None)
                or p.pop("content", None)
                or p.pop("text", None)
                or p.pop("raw", None)
                or ""
            )

        # LLMs sometimes emit Jira-style "issue_key" ("owner/repo#123") or a
        # full GitHub URL instead of separate owner/repo/issue_number fields.
        # Parse those variants so the executor receives the fields it expects.
        issue_ref = (
            p.pop("issue_key", None)
            or p.pop("issueKey", None)
            or p.pop("issue_url", None)
            or p.pop("pr_key", None)
            or p.pop("pr_url", None)
        )
        if issue_ref and isinstance(issue_ref, str):
            m = _ISSUE_KEY_RE.search(issue_ref.strip())
            if m:
                p.setdefault("owner", m.group(1))
                p.setdefault("repo", m.group(2))
                num = int(m.group(3))
                if action_type in ("approve_pr", "merge_pr", "request_changes", "comment_pr_line"):
                    p.setdefault("pr_number", num)
                else:
                    p.setdefault("issue_number", num)

        # Coerce issue_number / pr_number to int
        for key in ("issue_number", "pr_number"):
            if key in p:
                try:
                    p[key] = int(p[key])
                except (ValueError, TypeError):
                    pass

        # For PR review actions, ensure pr_number is set
        if action_type in ("approve_pr", "request_changes", "merge_pr") and "pr_number" not in p:
            p["pr_number"] = p.pop("issue_number", None) or p.pop("number", None) or 0

        # Merge method default
        if action_type == "merge_pr" and "merge_method" not in p:
            p["merge_method"] = p.pop("merge_strategy", None) or "squash"

        return p

    def identifiers_from_event(
        self,
        action_type: str,
        event_id: str | None,
        content_metadata: dict,
        event_row: dict,
        self_emails: set[str] | None = None,
    ) -> dict:
        """Derive GitHub identifiers deterministically from the source event.

        Preferred source is ``content_metadata['repo']`` (format ``"owner/name"``)
        because owner/repo names can contain underscores, which makes the
        ``evt_github_..._num_ts`` regex ambiguous. Falls back to the event_id.
        """
        ids: dict = {}

        # owner + repo — prefer metadata
        repo_full = (content_metadata or {}).get("repo") or ""
        if "/" in repo_full:
            owner, repo = repo_full.split("/", 1)
            ids["owner"] = owner
            ids["repo"] = repo

        # Number (issue or PR) — metadata (for comment events) or event_id suffix
        num: int | None = None
        meta_num = (content_metadata or {}).get("issue_number")
        if isinstance(meta_num, int):
            num = meta_num
        elif isinstance(meta_num, str) and meta_num.isdigit():
            num = int(meta_num)

        is_pr = bool((content_metadata or {}).get("is_pr"))

        if num is None and event_id:
            m = _EVENT_ID_RE.match(event_id)
            if m:
                num = int(m.group("num"))
                if m.group("kind") == "pr":
                    is_pr = True
                elif m.group("kind") == "issue":
                    is_pr = False
                # 'comment' kind: num is comment_id, not issue_number — skip
                if m.group("kind") == "comment":
                    num = None

        # Route to pr_number vs issue_number.  For PR-only actions we always
        # want pr_number.  For actions that target issues (comment/close), we
        # use issue_number.  create_issue/create_pr take neither.
        pr_actions = ("approve_pr", "merge_pr", "request_changes", "comment_pr_line")
        issue_actions = ("comment", "close_issue")
        if num is not None:
            if action_type in pr_actions:
                ids["pr_number"] = num
            elif action_type in issue_actions:
                # If the event is a PR-kind but the action is a generic comment,
                # GitHub's REST API treats PR comments as issue comments — still
                # use issue_number.
                ids["issue_number"] = num
            elif is_pr:
                ids["pr_number"] = num
            else:
                ids["issue_number"] = num

        return ids

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors."""
        errors = []

        if not payload.get("owner"):
            errors.append("Missing 'owner' (GitHub repository owner)")
        if not payload.get("repo"):
            errors.append("Missing 'repo' (GitHub repository name)")

        if action_type in ("comment", "close_issue", "create_issue"):
            if action_type != "create_issue" and not payload.get("issue_number"):
                errors.append("Missing 'issue_number'")

        if action_type in ("approve_pr", "request_changes", "merge_pr", "comment_pr_line"):
            if not payload.get("pr_number"):
                errors.append("Missing 'pr_number'")

        if action_type == "request_changes":
            if not payload.get("comment"):
                errors.append("Missing 'comment' body for request_changes")

        if action_type == "create_issue":
            if not payload.get("title"):
                errors.append("Missing issue 'title'")

        if action_type == "create_pr":
            if not payload.get("title"):
                errors.append("Missing PR 'title'")
            if not payload.get("head"):
                errors.append("Missing 'head' branch")
            if not payload.get("base"):
                errors.append("Missing 'base' branch")

        return errors


PLATFORM = GithubPlatform()
